import logging
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import secrets
from typing import Annotated, TypeVar
from urllib.parse import quote

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.core.config import get_settings
from app.core.rate_limit import client_ip as _client_ip
from app.core.rate_limit import enforce as _enforce_rate_limit
from app.db.session import AsyncSessionLocal, Base, engine, get_session
from app.security import (
    create_token,
    decode_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    verify_password,
)
from app.services.email import (
    email_status,
    send_email_with_attachment,
    send_customer_welcome_email,
    send_job_complete_email,
    send_password_changed_notification,
    send_password_reset_email,
    send_test_email,
    send_user_welcome_email,
    send_worker_welcome_email,
)
from app.services.inventory import (
    consume_stock,
    refund_stock_for_job,
    stock_balance,
)
from app.services.numbering import next_number
from app.services.pdf import build_invoice_pdf

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Lifespan — replaces deprecated @app.on_event("startup")
# --------------------------------------------------------------------------- #


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with AsyncSessionLocal() as session:
        await seed_reference_data(session)
    yield
    await engine.dispose()


_settings = get_settings()
# Hide OpenAPI / Swagger / ReDoc unless the operator explicitly opts in via
# ``EXPOSE_API_DOCS=true``. The public deploy doesn't need a discoverable
# schema, and the dev experience can always flip the flag locally.
app = FastAPI(
    title="Job Card Automation System API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _settings.expose_api_docs else None,
    redoc_url="/redoc" if _settings.expose_api_docs else None,
    openapi_url="/openapi.json" if _settings.expose_api_docs else None,
)

# CORS — permissive for local dev; configure ``CORS_ALLOW_ORIGINS`` in ``.env``
# with a comma-separated list of real origins before hosting.
_cors_origins = [o.strip() for o in _settings.cors_allow_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ModelT = TypeVar("ModelT", bound=Base)


DEFAULT_WORK_TYPES = [
    "Body work",
    "Engine",
    "Gearbox",
    "Electrical",
    "Suspension",
    "Brakes",
    "Paint work",
    "Diagnostics",
    "General service",
]

DEFAULT_COSTS = [
    ("Service cost", None),
    ("Labour cost", None),
    ("Towing cost", None),
    ("Inspection cost", None),
]


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> models.User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required")
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = await session.get(models.User, int(payload["sub"]))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive")

    # Token's pwd_at must match (or pre-date with no change recorded) the
    # user's current password_changed_at. Any password change invalidates
    # every token issued before the change — effectively a session reset.
    token_pwd_at = int(payload.get("pwd_at", 0) or 0)
    current_pwd_at = int(user.password_changed_at.timestamp()) if user.password_changed_at else 0
    if current_pwd_at > token_pwd_at:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session ended because the password was changed. Please sign in again.",
        )
    return user


def require_admin(current_user: Annotated[models.User, Depends(get_current_user)]) -> models.User:
    if current_user.role != models.UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an admin can change setup data",
        )
    return current_user


async def list_records(session: AsyncSession, model: type[ModelT]) -> Sequence[ModelT]:
    result = await session.scalars(select(model).order_by(model.id.desc()))
    return result.all()


async def create_record(session: AsyncSession, model: type[ModelT], payload) -> ModelT:
    record = model(**payload.model_dump())
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def update_record(session: AsyncSession, model: type[ModelT], record_id: int, payload) -> ModelT:
    record = await session.get(model, record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(record, key, value)
    await session.commit()
    await session.refresh(record)
    return record


async def delete_record(session: AsyncSession, model: type[ModelT], record_id: int) -> dict:
    record = await session.get(model, record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    if hasattr(record, "is_active"):
        record.is_active = False
        await session.commit()
        return {"deactivated": True, "id": record_id}
    await session.delete(record)
    await session.commit()
    return {"deleted": True, "id": record_id}


async def ensure_local_schema() -> None:
    """Idempotent DDL fix-ups for in-place schema evolution without Alembic.

    Each statement is ``ADD COLUMN IF NOT EXISTS`` / ``UPDATE … WHERE NULL`` so
    running it on a fresh DB is a no-op and running it after an upgrade
    backfills the new columns. Replace with Alembic before production hosting.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text("ALTER TABLE workers ADD COLUMN IF NOT EXISTS email VARCHAR(255)")
        )
        await conn.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS car_type VARCHAR(120)"))
        await conn.execute(
            text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true")
        )
        await conn.execute(
            text("ALTER TABLE customer_vehicles ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true")
        )
        await conn.execute(
            text("ALTER TABLE work_types ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true")
        )
        await conn.execute(
            text("ALTER TABLE garage_costs ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true")
        )
        await conn.execute(
            text("ALTER TABLE garage_items ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true")
        )
        await conn.execute(
            text(
                "ALTER TABLE inventory_receipts "
                "ADD COLUMN IF NOT EXISTS default_sale_price NUMERIC(12, 2)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE inventory_receipts "
                "ADD COLUMN IF NOT EXISTS supplier_invoice_file_path VARCHAR(500)"
            )
        )
        await conn.execute(
            text("ALTER TABLE inventory_receipts ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS status VARCHAR(30) DEFAULT 'in_progress'")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS intake_mode VARCHAR(30) DEFAULT 'in_shop'")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS delivery_mode VARCHAR(30) DEFAULT 'in_shop'")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS picked_up_by_user_id INTEGER")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS vehicle_id INTEGER")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS pickup_cost_id INTEGER")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS pickup_cost_amount NUMERIC(12, 2)")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS dropoff_cost_id INTEGER")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS dropoff_cost_amount NUMERIC(12, 2)")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS fuel_level VARCHAR(60)")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS mileage VARCHAR(60)")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS present_items TEXT")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS other_present_items TEXT")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS condition_notes TEXT")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS invoice_notes TEXT")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS subtotal NUMERIC(12, 2) DEFAULT 0")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS discount_amount NUMERIC(12, 2) DEFAULT 0")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS total_amount NUMERIC(12, 2) DEFAULT 0")
        )
        await conn.execute(
            text("ALTER TABLE job_cards ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true")
        )
        await conn.execute(
            text("ALTER TABLE job_card_works ADD COLUMN IF NOT EXISTS notes TEXT")
        )
        await conn.execute(
            text("ALTER TABLE job_card_works ADD COLUMN IF NOT EXISTS labour_amount NUMERIC(12, 2) DEFAULT 0")
        )
        await conn.execute(
            text("ALTER TABLE job_card_works ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true")
        )
        await conn.execute(
            text("ALTER TABLE job_card_items ADD COLUMN IF NOT EXISTS unit_cost NUMERIC(12, 2)")
        )
        await conn.execute(
            text("ALTER TABLE job_card_items ADD COLUMN IF NOT EXISTS unit_price NUMERIC(12, 2) DEFAULT 0")
        )
        await conn.execute(
            text("ALTER TABLE job_card_items ADD COLUMN IF NOT EXISTS amount NUMERIC(12, 2) DEFAULT 0")
        )
        await conn.execute(
            text("ALTER TABLE job_card_items ADD COLUMN IF NOT EXISTS notes TEXT")
        )
        await conn.execute(
            text("ALTER TABLE job_card_items ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true")
        )
        await conn.execute(
            text("ALTER TABLE job_card_costs ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true")
        )
        await conn.execute(
            text("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS payment_status VARCHAR(30) DEFAULT 'not_paid'")
        )
        await conn.execute(
            text("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS amount_paid NUMERIC(12, 2) DEFAULT 0")
        )
        await conn.execute(
            text("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS tax_rate NUMERIC(5, 2) DEFAULT 0")
        )
        await conn.execute(
            text("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS vat_amount NUMERIC(12, 2) DEFAULT 0")
        )
        await conn.execute(
            text("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS discount_amount NUMERIC(12, 2) DEFAULT 0")
        )
        await conn.execute(
            text("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS is_sent_email BOOLEAN DEFAULT false")
        )
        await conn.execute(
            text("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS is_sent_whatsapp BOOLEAN DEFAULT false")
        )
        await conn.execute(
            text("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true")
        )
        # Password-change tracking column used to invalidate stale tokens.
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ")
        )
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    ALTER TYPE jobcardstatus ADD VALUE IF NOT EXISTS 'complete';
                    ALTER TYPE jobcardstatus ADD VALUE IF NOT EXISTS 'cancelled';
                EXCEPTION
                    WHEN undefined_object THEN NULL;
                END $$;
                """
            )
        )
        await conn.execute(
            text("UPDATE invoices SET payment_status = 'not_paid' WHERE payment_status IS NULL")
        )
        await conn.execute(text("UPDATE invoices SET amount_paid = 0 WHERE amount_paid IS NULL"))
        await conn.execute(text("UPDATE invoices SET tax_rate = 0 WHERE tax_rate IS NULL"))
        await conn.execute(text("UPDATE invoices SET vat_amount = 0 WHERE vat_amount IS NULL"))
        await conn.execute(text("UPDATE invoices SET discount_amount = 0 WHERE discount_amount IS NULL"))
        await conn.execute(text("UPDATE invoices SET is_sent_email = false WHERE is_sent_email IS NULL"))
        await conn.execute(text("UPDATE invoices SET is_sent_whatsapp = false WHERE is_sent_whatsapp IS NULL"))
        await conn.execute(text("UPDATE invoices SET is_active = true WHERE is_active IS NULL"))
        await conn.execute(text("UPDATE job_cards SET status = 'in_progress' WHERE status IS NULL"))
        await conn.execute(text("UPDATE job_cards SET intake_mode = 'in_shop' WHERE intake_mode IS NULL"))
        await conn.execute(text("UPDATE job_cards SET delivery_mode = 'in_shop' WHERE delivery_mode IS NULL"))
        await conn.execute(
            text(
                """
                INSERT INTO customer_vehicles (
                    customer_id,
                    registration,
                    make,
                    vehicle_type,
                    year,
                    description,
                    is_active
                )
                SELECT
                    c.id,
                    c.car_registration,
                    c.car_model,
                    c.car_type,
                    c.car_year,
                    c.car_description,
                    COALESCE(c.is_active, true)
                FROM customers c
                WHERE c.car_registration IS NOT NULL
                  AND c.car_model IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1
                    FROM customer_vehicles cv
                    WHERE cv.customer_id = c.id
                      AND cv.registration = c.car_registration
                  )
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE job_cards jc
                SET vehicle_id = cv.id
                FROM customer_vehicles cv
                WHERE jc.vehicle_id IS NULL
                  AND cv.customer_id = jc.customer_id
                """
            )
        )
        await conn.execute(text("UPDATE job_cards SET subtotal = 0 WHERE subtotal IS NULL"))
        await conn.execute(text("UPDATE job_cards SET discount_amount = 0 WHERE discount_amount IS NULL"))
        await conn.execute(text("UPDATE job_cards SET total_amount = 0 WHERE total_amount IS NULL"))
        await conn.execute(text("UPDATE job_cards SET is_active = true WHERE is_active IS NULL"))
        await conn.execute(text("UPDATE job_card_works SET labour_amount = 0 WHERE labour_amount IS NULL"))
        await conn.execute(text("UPDATE job_card_works SET is_active = true WHERE is_active IS NULL"))
        await conn.execute(text("UPDATE job_card_items SET unit_price = 0 WHERE unit_price IS NULL"))
        await conn.execute(text("UPDATE job_card_items SET amount = 0 WHERE amount IS NULL"))
        await conn.execute(text("UPDATE job_card_items SET is_active = true WHERE is_active IS NULL"))
        await conn.execute(text("UPDATE job_card_costs SET is_active = true WHERE is_active IS NULL"))
        await conn.execute(text("UPDATE inventory_receipts SET is_active = true WHERE is_active IS NULL"))


async def seed_reference_data(session: AsyncSession) -> None:
    work_count = await session.scalar(select(func.count()).select_from(models.WorkType))
    if work_count == 0:
        session.add_all([models.WorkType(name=name, is_active=True) for name in DEFAULT_WORK_TYPES])

    cost_count = await session.scalar(select(func.count()).select_from(models.GarageCost))
    if cost_count == 0:
        session.add_all(
            [
                models.GarageCost(name=name, default_amount=amount, is_active=True)
                for name, amount in DEFAULT_COSTS
            ]
        )
    await session.commit()


async def active_admin_count(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count()).select_from(models.User).where(
            models.User.role == models.UserRole.admin,
            models.User.is_active.is_(True),
        )
    )


def money(value) -> Decimal:
    """Round to 2dp. Local convenience; the same shape lives in service
    modules but is kept here for the existing call sites in this file."""
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def sync_invoice_payment_status(invoice: models.Invoice) -> None:
    """Derive payment status from the stored total and amount received.

    This keeps receivables honest when a job-card edit changes the invoice
    total after payment has already been recorded.
    """
    total = money(invoice.total_amount)
    paid = money(invoice.amount_paid)
    if total <= 0 or paid >= total:
        invoice.payment_status = models.PaymentStatus.paid
    elif paid > 0:
        invoice.payment_status = models.PaymentStatus.partially_paid
    else:
        invoice.payment_status = models.PaymentStatus.not_paid


async def active_record(
    session: AsyncSession,
    model: type[ModelT],
    record_id: int,
    label: str,
) -> ModelT:
    record = await session.get(model, record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    if hasattr(record, "is_active") and not record.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{label} is inactive and cannot be used for new transactions.",
        )
    return record


async def optional_active_record(
    session: AsyncSession,
    model: type[ModelT],
    record_id: int | None,
    label: str,
) -> ModelT | None:
    if record_id is None:
        return None
    return await active_record(session, model, record_id, label)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/auth/bootstrap-status")
async def bootstrap_status(session: Annotated[AsyncSession, Depends(get_session)]) -> dict:
    user_count = await session.scalar(select(func.count()).select_from(models.User))
    return {"needs_bootstrap": user_count == 0}


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@app.post("/auth/login", response_model=schemas.LoginResponse)
async def login(
    payload: schemas.LoginRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Sign in or — on an empty DB and a loopback request — bootstrap the
    first admin.

    Two robustness changes over the original:

    1. Bootstrap is restricted to the loopback interface. Without this, anyone
       who reaches an unbootstrapped instance over the network can claim it.
       For cloud hosting, ship the first-admin creation via a CLI command and
       remove the bootstrap path entirely.
    2. Concurrent bootstrap requests previously raised a 500 on the unique
       email constraint. We now treat an IntegrityError on bootstrap as
       "someone else got there first" and fall through to the standard
       password-check flow.
    """
    email_normalised = str(payload.email).lower()
    rate_key = f"login:{email_normalised}|{_client_ip(request)}"
    _enforce_rate_limit(
        rate_key,
        max_hits=10,
        window_seconds=60,
        detail="Too many sign-in attempts. Wait a minute and try again.",
    )

    user_count = await session.scalar(select(func.count()).select_from(models.User))
    is_bootstrap_admin = False
    user: models.User | None = None

    if user_count == 0:
        # Bootstrap is allowed if EITHER the request comes from the loopback
        # interface (local console) OR the operator has configured a one-time
        # ``BOOTSTRAP_SECRET`` env var and the request supplies it via the
        # ``X-Bootstrap-Secret`` header. The second path is what hosted
        # deployments (Easypanel etc.) use — the proxy network never reports
        # 127.0.0.1, so a header secret is the only way to do the first sign-in
        # over the public domain without SSH-ing into a container.
        is_loopback = _client_ip(request) in _LOOPBACK_HOSTS
        configured_secret = (get_settings().bootstrap_secret or "").strip()
        supplied_secret = (request.headers.get("X-Bootstrap-Secret") or "").strip()
        secret_match = bool(configured_secret) and supplied_secret == configured_secret
        if not (is_loopback or secret_match):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "The first administrator can only be created from the local "
                    "machine, OR by supplying a matching X-Bootstrap-Secret "
                    "header that matches the BOOTSTRAP_SECRET env var. Open "
                    "the app on the server's own console, or set "
                    "BOOTSTRAP_SECRET in .env and pass it as the setup secret "
                    "on the first sign-in."
                ),
            )
        full_name = payload.full_name or email_normalised.split("@", 1)[0]
        candidate = models.User(
            full_name=full_name,
            email=email_normalised,
            password_hash=hash_password(payload.password),
            role=models.UserRole.admin,
            is_active=True,
            password_changed_at=datetime.now(UTC),
        )
        session.add(candidate)
        try:
            await session.commit()
        except IntegrityError:
            # Lost the race against another concurrent bootstrap. Fall through
            # to the regular password-check path below; whoever won the race
            # is now the legitimate admin.
            await session.rollback()
        else:
            await session.refresh(candidate)
            user = candidate
            is_bootstrap_admin = True
            background_tasks.add_task(
                send_user_welcome_email,
                user.email,
                user.full_name,
                user.role.value,
                True,
            )

    if user is None:
        user = await session.scalar(
            select(models.User).where(models.User.email == email_normalised)
        )
        if not user or not verify_password(payload.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")

        # Quietly upgrade legacy / low-iteration hashes on a successful login.
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(payload.password)
            await session.commit()

    return schemas.LoginResponse(
        access_token=create_token(user.id, user.role.value, user.password_changed_at),
        role=user.role,
        full_name=user.full_name,
        is_bootstrap_admin=is_bootstrap_admin,
    )


@app.post("/auth/forgot-password", response_model=schemas.MessageResponse)
async def forgot_password(
    payload: schemas.ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Issue a single-use, time-limited reset token and email it to the user.

    Returns a constant message regardless of whether the address is on file
    (prevents account-enumeration). Rate-limited per email *and* per client IP
    so the endpoint can't be abused to flood inboxes.
    """
    email_normalised = str(payload.email).lower()
    public_message = "If that email exists, a password reset link has been sent."

    # Tight per-email cap so a single inbox can't be flooded.
    _enforce_rate_limit(
        f"forgot-email:{email_normalised}",
        max_hits=3,
        window_seconds=3600,
        detail="Too many reset requests for that email. Try again in an hour.",
    )
    # Looser per-IP cap as a backstop against enumeration sweeps.
    _enforce_rate_limit(
        f"forgot-ip:{_client_ip(request)}",
        max_hits=20,
        window_seconds=3600,
        detail="Too many reset requests from this address. Try again later.",
    )

    user = await session.scalar(
        select(models.User).where(
            models.User.email == email_normalised,
            models.User.is_active.is_(True),
        )
    )
    if not user:
        # Constant-shape response so a probe can't time the absence.
        return schemas.MessageResponse(message=public_message)

    now = datetime.now(UTC)
    # Invalidate every outstanding token for this user — only the freshest
    # link should be usable. Mitigates a stolen-old-link scenario.
    old_tokens = await session.scalars(
        select(models.PasswordResetToken).where(
            models.PasswordResetToken.user_id == user.id,
            models.PasswordResetToken.used_at.is_(None),
        )
    )
    for stale in old_tokens:
        stale.used_at = now

    raw_token = secrets.token_urlsafe(32)
    settings = get_settings()
    expires_at = now + timedelta(minutes=settings.password_reset_ttl_minutes)
    reset_token = models.PasswordResetToken(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=expires_at,
    )
    session.add(reset_token)
    await session.commit()

    reset_url = f"{settings.frontend_url.rstrip('/')}/?reset_token={quote(raw_token)}"
    background_tasks.add_task(
        send_password_reset_email,
        user.email,
        user.full_name,
        reset_url,
        settings.password_reset_ttl_minutes,
    )
    logger.info("Password reset link emailed to user_id=%s", user.id)
    return schemas.MessageResponse(message=public_message)


@app.post("/auth/reset-password", response_model=schemas.MessageResponse)
async def reset_password(
    payload: schemas.ResetPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Consume a reset token and set a new password.

    Side effects beyond the basic reset:

    - Bumps ``user.password_changed_at`` to the current UTC time, which
      invalidates every JWT issued before this moment (the ``pwd_at`` check
      in ``get_current_user``). All sessions are signed out.
    - Sends a security-notification email so the legitimate owner sees the
      change happen even if they didn't initiate it.
    """
    _enforce_rate_limit(
        f"reset-ip:{_client_ip(request)}",
        max_hits=10,
        window_seconds=3600,
        detail="Too many reset attempts from this address. Try again later.",
    )

    now = datetime.now(UTC)
    reset_token = await session.scalar(
        select(models.PasswordResetToken).where(
            models.PasswordResetToken.token_hash == hash_token(payload.token),
            models.PasswordResetToken.used_at.is_(None),
            models.PasswordResetToken.expires_at > now,
        )
    )
    if not reset_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset link is invalid or has expired.",
        )
    user = await session.get(models.User, reset_token.user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset link is invalid or has expired.",
        )

    user.password_hash = hash_password(payload.password)
    user.password_changed_at = now
    reset_token.used_at = now
    await session.commit()

    background_tasks.add_task(
        send_password_changed_notification,
        user.email,
        user.full_name,
        now.strftime("%Y-%m-%d %H:%M"),
        "you (via password reset)",
    )
    logger.info("Password reset succeeded for user_id=%s", user.id)
    return schemas.MessageResponse(
        message="Password updated. You can now sign in with the new password."
    )


@app.post("/auth/change-password", response_model=schemas.MessageResponse)
async def change_password(
    payload: schemas.ChangePasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[models.User, Depends(get_current_user)],
):
    """In-session password change for an authenticated user.

    Requires the current password (defence-in-depth against a stolen but
    short-TTL session token). Same session-invalidation + notification side
    effects as the reset flow.
    """
    _enforce_rate_limit(
        f"change-pw-user:{current_user.id}",
        max_hits=5,
        window_seconds=3600,
        detail="Too many password changes for this account. Try again later.",
    )

    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from the current one.",
        )

    now = datetime.now(UTC)
    current_user.password_hash = hash_password(payload.new_password)
    current_user.password_changed_at = now
    await session.commit()

    background_tasks.add_task(
        send_password_changed_notification,
        current_user.email,
        current_user.full_name,
        now.strftime("%Y-%m-%d %H:%M"),
        "you (signed in change)",
    )
    return schemas.MessageResponse(
        message="Password updated. You'll need to sign in again on your other devices."
    )


@app.get("/auth/me", response_model=schemas.UserRead)
async def get_me(current_user: Annotated[models.User, Depends(get_current_user)]):
    return current_user


@app.get("/lookups")
async def lookups() -> dict:
    return {
        "kenya_counties": KENYA_COUNTIES,
        "car_models": CAR_MODELS,
        "service_options": [option.value for option in models.ServiceOption],
        "roles": [role.value for role in models.UserRole],
    }


@app.get("/email/status", response_model=schemas.EmailStatusRead)
async def get_email_status(
    _: Annotated[models.User, Depends(require_admin)],
):
    return email_status()


@app.post("/email/test")
async def test_email(
    payload: schemas.TestEmailRequest,
    _: Annotated[models.User, Depends(require_admin)],
):
    if not email_status()["configured"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SMTP is not fully configured or is disabled",
        )
    sent = send_test_email(str(payload.to_email))
    if not sent:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="SMTP test email could not be sent",
        )
    return {"sent": True}


@app.post("/email/resend-my-welcome")
async def resend_my_welcome_email(
    current_user: Annotated[models.User, Depends(get_current_user)],
):
    if not email_status()["configured"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SMTP is not fully configured or is disabled",
        )
    sent = send_user_welcome_email(
        current_user.email,
        current_user.full_name,
        current_user.role.value,
        current_user.role == models.UserRole.admin,
    )
    if not sent:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Welcome email could not be sent. Check the SMTP credentials.",
        )
    return {"sent": True}


@app.get("/users", response_model=list[schemas.UserRead])
async def get_users(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
):
    return await list_records(session, models.User)


@app.post("/users", response_model=schemas.UserRead)
async def create_user(
    payload: schemas.UserCreate,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    user = models.User(
        full_name=payload.full_name,
        email=str(payload.email).lower(),
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=payload.is_active,
        password_changed_at=datetime.now(UTC),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        ) from exc
    await session.refresh(user)
    background_tasks.add_task(
        send_user_welcome_email,
        user.email,
        user.full_name,
        user.role.value,
        False,
    )
    return user


@app.patch("/users/{record_id}", response_model=schemas.UserRead)
async def update_user(
    record_id: int,
    payload: schemas.UserUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    user = await session.get(models.User, record_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    updates = payload.model_dump(exclude_unset=True)
    will_remove_admin = (
        user.role == models.UserRole.admin
        and user.is_active
        and (
            updates.get("role") == models.UserRole.user
            or updates.get("is_active") is False
        )
    )
    if will_remove_admin and await active_admin_count(session) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Create another active admin before changing this admin account",
        )
    if "password" in updates:
        user.password_hash = hash_password(updates.pop("password"))
        # Bump the version stamp so any existing sessions for this user are
        # signed out — matches the reset / self-change behaviour.
        user.password_changed_at = datetime.now(UTC)
    for key, value in updates.items():
        setattr(user, key, value)
    await session.commit()
    await session.refresh(user)
    return user


@app.delete("/users/{record_id}")
async def remove_user(
    record_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    user = await session.get(models.User, record_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    if user.role == models.UserRole.admin and user.is_active and await active_admin_count(session) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Create another active admin before deleting this admin account",
        )
    user.is_active = False
    await session.commit()
    return {"deactivated": True, "id": record_id}


@app.get("/suppliers", response_model=list[schemas.SupplierRead])
async def get_suppliers(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
):
    return await list_records(session, models.Supplier)


@app.post("/suppliers", response_model=schemas.SupplierRead)
async def add_supplier(
    payload: schemas.SupplierCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await create_record(session, models.Supplier, payload)


@app.patch("/suppliers/{record_id}", response_model=schemas.SupplierRead)
async def edit_supplier(
    record_id: int,
    payload: schemas.SupplierUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await update_record(session, models.Supplier, record_id, payload)


@app.delete("/suppliers/{record_id}")
async def remove_supplier(
    record_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await delete_record(session, models.Supplier, record_id)


@app.get("/workers", response_model=list[schemas.WorkerRead])
async def get_workers(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
):
    return await list_records(session, models.Worker)


@app.post("/workers", response_model=schemas.WorkerRead)
async def add_worker(
    payload: schemas.WorkerCreate,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    worker = await create_record(session, models.Worker, payload)
    if worker.email:
        background_tasks.add_task(send_worker_welcome_email, worker.email, worker.full_name)
    return worker


@app.patch("/workers/{record_id}", response_model=schemas.WorkerRead)
async def edit_worker(
    record_id: int,
    payload: schemas.WorkerUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await update_record(session, models.Worker, record_id, payload)


@app.delete("/workers/{record_id}")
async def remove_worker(
    record_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await delete_record(session, models.Worker, record_id)


@app.get("/customers", response_model=list[schemas.CustomerRead])
async def get_customers(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
):
    return await list_records(session, models.Customer)


@app.post("/customers", response_model=schemas.CustomerRead)
async def add_customer(
    payload: schemas.CustomerCreate,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    data = payload.model_dump()
    car_registration = data.pop("car_registration").upper()
    car_make = data.pop("car_make")
    car_type = data.pop("car_type", None)
    car_year = data.pop("car_year", None)
    car_description = data.pop("car_description", None)
    customer = models.Customer(
        **data,
        car_registration=car_registration,
        car_model=car_make,
        car_type=car_type,
        car_year=car_year,
        car_description=car_description,
    )
    session.add(customer)
    await session.flush()
    session.add(
        models.CustomerVehicle(
            customer_id=customer.id,
            registration=car_registration,
            make=car_make,
            vehicle_type=car_type,
            year=car_year,
            description=car_description,
            is_active=True,
        )
    )
    await session.commit()
    await session.refresh(customer)
    if customer.email:
        background_tasks.add_task(
            send_customer_welcome_email,
            customer.email,
            customer.full_name,
            customer.car_registration,
            f"{customer.car_model} {customer.car_type or ''}".strip(),
        )
    return customer


@app.patch("/customers/{record_id}", response_model=schemas.CustomerRead)
async def edit_customer(
    record_id: int,
    payload: schemas.CustomerUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    customer = await session.get(models.Customer, record_id)
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    updates = payload.model_dump(exclude_unset=True)
    field_map = {
        "car_make": "car_model",
        "car_registration": "car_registration",
        "car_type": "car_type",
        "car_year": "car_year",
        "car_description": "car_description",
    }
    for key, value in updates.items():
        setattr(customer, field_map.get(key, key), value)
    await session.commit()
    await session.refresh(customer)
    return customer


@app.delete("/customers/{record_id}")
async def remove_customer(
    record_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await delete_record(session, models.Customer, record_id)


@app.get("/customer-vehicles", response_model=list[schemas.CustomerVehicleRead])
async def get_customer_vehicles(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
    customer_id: int | None = None,
):
    if customer_id is not None:
        result = await session.scalars(
            select(models.CustomerVehicle)
            .where(models.CustomerVehicle.customer_id == customer_id)
            .order_by(models.CustomerVehicle.id.desc())
        )
        return result.all()
    return await list_records(session, models.CustomerVehicle)


@app.post("/customer-vehicles", response_model=schemas.CustomerVehicleRead)
async def add_customer_vehicle(
    payload: schemas.CustomerVehicleCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    await active_record(session, models.Customer, payload.customer_id, "Customer")
    return await create_record(session, models.CustomerVehicle, payload)


@app.patch("/customer-vehicles/{record_id}", response_model=schemas.CustomerVehicleRead)
async def edit_customer_vehicle(
    record_id: int,
    payload: schemas.CustomerVehicleUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    if payload.customer_id is not None:
        await active_record(session, models.Customer, payload.customer_id, "Customer")
    return await update_record(session, models.CustomerVehicle, record_id, payload)


@app.post("/customer-vehicles/{record_id}/transfer", response_model=schemas.CustomerVehicleRead)
async def transfer_customer_vehicle(
    record_id: int,
    payload: schemas.VehicleTransferRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    vehicle = await session.get(models.CustomerVehicle, record_id)
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")
    if not vehicle.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vehicle is inactive and cannot be transferred.",
        )
    await active_record(session, models.Customer, payload.new_customer_id, "New owner")
    if vehicle.customer_id == payload.new_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This customer already owns the vehicle",
        )

    session.add(
        models.VehicleOwnershipHistory(
            vehicle_id=vehicle.id,
            previous_customer_id=vehicle.customer_id,
            new_customer_id=payload.new_customer_id,
            notes=payload.notes,
        )
    )
    vehicle.customer_id = payload.new_customer_id
    await session.commit()
    await session.refresh(vehicle)
    return vehicle


@app.get("/vehicle-ownership-history", response_model=list[schemas.VehicleOwnershipHistoryRead])
async def get_vehicle_ownership_history(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
    vehicle_id: int | None = None,
):
    stmt = select(models.VehicleOwnershipHistory).order_by(models.VehicleOwnershipHistory.id.desc())
    if vehicle_id is not None:
        stmt = stmt.where(models.VehicleOwnershipHistory.vehicle_id == vehicle_id)
    result = await session.scalars(stmt)
    return result.all()


@app.delete("/customer-vehicles/{record_id}")
async def remove_customer_vehicle(
    record_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await delete_record(session, models.CustomerVehicle, record_id)


@app.get("/work-types", response_model=list[schemas.WorkTypeRead])
async def get_work_types(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
):
    return await list_records(session, models.WorkType)


@app.post("/work-types", response_model=schemas.WorkTypeRead)
async def add_work_type(
    payload: schemas.WorkTypeCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await create_record(session, models.WorkType, payload)


@app.patch("/work-types/{record_id}", response_model=schemas.WorkTypeRead)
async def edit_work_type(
    record_id: int,
    payload: schemas.WorkTypeUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await update_record(session, models.WorkType, record_id, payload)


@app.delete("/work-types/{record_id}")
async def remove_work_type(
    record_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await delete_record(session, models.WorkType, record_id)


@app.get("/garage-costs", response_model=list[schemas.GarageCostRead])
async def get_garage_costs(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
):
    return await list_records(session, models.GarageCost)


@app.post("/garage-costs", response_model=schemas.GarageCostRead)
async def add_garage_cost(
    payload: schemas.GarageCostCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await create_record(session, models.GarageCost, payload)


@app.patch("/garage-costs/{record_id}", response_model=schemas.GarageCostRead)
async def edit_garage_cost(
    record_id: int,
    payload: schemas.GarageCostUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await update_record(session, models.GarageCost, record_id, payload)


@app.delete("/garage-costs/{record_id}")
async def remove_garage_cost(
    record_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await delete_record(session, models.GarageCost, record_id)


@app.get("/garage-items", response_model=list[schemas.GarageItemRead])
async def get_garage_items(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
):
    return await list_records(session, models.GarageItem)


@app.post("/garage-items", response_model=schemas.GarageItemRead)
async def add_garage_item(
    payload: schemas.GarageItemCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await create_record(session, models.GarageItem, payload)


@app.patch("/garage-items/{record_id}", response_model=schemas.GarageItemRead)
async def edit_garage_item(
    record_id: int,
    payload: schemas.GarageItemUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await update_record(session, models.GarageItem, record_id, payload)


@app.delete("/garage-items/{record_id}")
async def remove_garage_item(
    record_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    return await delete_record(session, models.GarageItem, record_id)


@app.get("/inventory/receipts", response_model=list[schemas.InventoryReceiptRead])
async def get_inventory_receipts(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
):
    receipts = await list_records(session, models.InventoryReceipt)
    items = {item.id: item.name for item in (await session.scalars(select(models.GarageItem))).all()}
    suppliers = {supplier.id: supplier.name for supplier in (await session.scalars(select(models.Supplier))).all()}
    return [
        {
            **schemas.InventoryReceiptRead.model_validate(receipt).model_dump(),
            "item_name": items.get(receipt.garage_item_id),
            "supplier_name": suppliers.get(receipt.supplier_id),
        }
        for receipt in receipts
    ]


@app.post("/inventory/receipts", response_model=schemas.InventoryReceiptRead)
async def receive_inventory(
    payload: schemas.InventoryReceiptCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    await active_record(session, models.GarageItem, payload.garage_item_id, "Garage item")
    await active_record(session, models.Supplier, payload.supplier_id, "Supplier")
    receipt = models.InventoryReceipt(
        **payload.model_dump(),
        remaining_quantity=money(payload.quantity),
    )
    session.add(receipt)
    await session.flush()
    session.add(
        models.StockMovement(
            garage_item_id=receipt.garage_item_id,
            supplier_id=receipt.supplier_id,
            inventory_receipt_id=receipt.id,
            movement_type=models.StockMovementType.stock_in,
            quantity=receipt.quantity,
            unit_cost=receipt.unit_cost,
            notes=receipt.notes,
        )
    )
    await session.commit()
    await session.refresh(receipt)
    return receipt


def _receipt_snapshot(receipt: models.InventoryReceipt) -> dict:
    """JSON-safe snapshot of a receipt — what gets pinned into the audit row."""
    return {
        "garage_item_id": receipt.garage_item_id,
        "supplier_id": receipt.supplier_id,
        "quantity": str(receipt.quantity),
        "remaining_quantity": str(receipt.remaining_quantity),
        "unit_cost": str(receipt.unit_cost),
        "default_sale_price": (
            str(receipt.default_sale_price) if receipt.default_sale_price is not None else None
        ),
        "supplier_invoice_ref": receipt.supplier_invoice_ref,
        "notes": receipt.notes,
        "is_active": receipt.is_active,
    }


@app.patch("/inventory/receipts/{receipt_id}", response_model=schemas.InventoryReceiptRead)
async def edit_inventory_receipt(
    receipt_id: int,
    payload: schemas.InventoryReceiptUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[models.User, Depends(require_admin)],
):
    """Correct a receipt that was logged with wrong details.

    Quantity edits clamp to the amount that's already been consumed: the new
    quantity can never be less than ``(old_quantity - remaining_quantity)``.
    Every successful edit writes a full before/after audit row with the
    actor and the required reason.
    """
    receipt = await session.get(models.InventoryReceipt, receipt_id)
    if not receipt or not receipt.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found")

    before = _receipt_snapshot(receipt)

    updates = payload.model_dump(exclude_unset=True)
    reason = updates.pop("reason")

    if "supplier_id" in updates and updates["supplier_id"] is not None:
        if await session.get(models.Supplier, updates["supplier_id"]) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Supplier not found"
            )

    if "quantity" in updates and updates["quantity"] is not None:
        new_quantity = money(updates["quantity"])
        already_consumed = money(receipt.quantity) - money(receipt.remaining_quantity)
        if new_quantity < already_consumed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"New quantity {new_quantity} is less than what's already been "
                    f"used ({already_consumed}). Issue a corrective receipt instead."
                ),
            )
        receipt.remaining_quantity = new_quantity - already_consumed
        receipt.quantity = new_quantity
        updates.pop("quantity")

    money_fields = {"unit_cost", "default_sale_price"}
    for key, value in updates.items():
        if value is None:
            continue
        if key in money_fields:
            value = money(value)
        setattr(receipt, key, value)

    after = _receipt_snapshot(receipt)

    session.add(
        models.InventoryReceiptAudit(
            inventory_receipt_id=receipt.id,
            actor_user_id=admin.id,
            action="edited",
            snapshot_before=before,
            snapshot_after=after,
            reason=reason,
        )
    )
    await session.commit()
    await session.refresh(receipt)
    return receipt


@app.delete("/inventory/receipts/{receipt_id}")
async def remove_inventory_receipt(
    receipt_id: int,
    payload: schemas.InventoryReceiptDeleteRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[models.User, Depends(require_admin)],
):
    """Soft-delete a receipt that was logged in error.

    Blocked if any of its stock has been consumed — admins must issue a
    corrective receipt or refund the affected job cards first. Writes a
    ``deleted`` audit row so the action is traceable.
    """
    receipt = await session.get(models.InventoryReceipt, receipt_id)
    if not receipt or not receipt.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found")

    if money(receipt.remaining_quantity) != money(receipt.quantity):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Cannot delete this receipt — some of its stock has already been "
                "consumed by a job card. Issue a corrective receipt instead."
            ),
        )

    before = _receipt_snapshot(receipt)
    receipt.is_active = False

    session.add(
        models.InventoryReceiptAudit(
            inventory_receipt_id=receipt.id,
            actor_user_id=admin.id,
            action="deleted",
            snapshot_before=before,
            snapshot_after=None,
            reason=payload.reason,
        )
    )
    await session.commit()
    return {"deleted": True, "id": receipt_id}


@app.get(
    "/inventory/receipts/{receipt_id}/audit",
    response_model=list[schemas.InventoryReceiptAuditRead],
)
async def get_receipt_audit(
    receipt_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    rows = (
        await session.scalars(
            select(models.InventoryReceiptAudit)
            .where(models.InventoryReceiptAudit.inventory_receipt_id == receipt_id)
            .order_by(models.InventoryReceiptAudit.id.desc())
        )
    ).all()
    payload = []
    for row in rows:
        actor = await session.get(models.User, row.actor_user_id)
        payload.append(
            {
                "id": row.id,
                "inventory_receipt_id": row.inventory_receipt_id,
                "actor_user_id": row.actor_user_id,
                "actor_name": actor.full_name if actor else None,
                "action": row.action,
                "snapshot_before": row.snapshot_before,
                "snapshot_after": row.snapshot_after,
                "reason": row.reason,
                "created_at": row.created_at,
            }
        )
    return payload


@app.get("/inventory/stock", response_model=list[schemas.StockBalanceRead])
async def get_inventory_stock(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
):
    items = await session.scalars(select(models.GarageItem).order_by(models.GarageItem.name))
    receipts = (
        await session.scalars(
            select(models.InventoryReceipt)
            .where(models.InventoryReceipt.is_active.is_(True))
            .order_by(models.InventoryReceipt.id.asc())
        )
    ).all()
    suppliers = {supplier.id: supplier.name for supplier in (await session.scalars(select(models.Supplier))).all()}
    rows = []
    for item in items:
        item_receipts = [receipt for receipt in receipts if receipt.garage_item_id == item.id]
        latest = item_receipts[-1] if item_receipts else None
        latest_sale_receipt = next(
            (
                receipt
                for receipt in reversed(item_receipts)
                if receipt.default_sale_price is not None and money(receipt.default_sale_price) > 0
            ),
            None,
        )
        rows.append(
            schemas.StockBalanceRead(
                garage_item_id=item.id,
                item_name=item.name,
                category=item.category,
                unit=item.unit,
                is_active=item.is_active,
                quantity_on_hand=sum((money(r.remaining_quantity) for r in item_receipts), Decimal("0.00")),
                latest_unit_cost=latest.unit_cost if latest else None,
                latest_sale_price=latest_sale_receipt.default_sale_price if latest_sale_receipt else None,
                latest_supplier=suppliers.get(latest.supplier_id) if latest else None,
            )
        )
    return rows


@app.get("/stock-movements", response_model=list[schemas.StockMovementRead])
async def get_stock_movements(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
):
    return await list_records(session, models.StockMovement)


async def resolve_job_customer_vehicle(
    session: AsyncSession,
    payload: schemas.JobCardCreate,
) -> tuple[int, int]:
    if payload.customer_id:
        customer = await active_record(session, models.Customer, payload.customer_id, "Customer")
        customer_id = customer.id
    elif payload.new_customer:
        if not payload.new_customer.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New customers on a job card must be active.",
            )
        data = payload.new_customer.model_dump()
        car_registration = data.pop("car_registration").upper()
        car_make = data.pop("car_make")
        car_type = data.pop("car_type", None)
        car_year = data.pop("car_year", None)
        car_description = data.pop("car_description", None)
        customer = models.Customer(
            **data,
            car_registration=car_registration,
            car_model=car_make,
            car_type=car_type,
            car_year=car_year,
            car_description=car_description,
        )
        session.add(customer)
        await session.flush()
        vehicle = models.CustomerVehicle(
            customer_id=customer.id,
            registration=car_registration,
            make=car_make,
            vehicle_type=car_type,
            year=car_year,
            description=car_description,
            is_active=True,
        )
        session.add(vehicle)
        await session.flush()
        return customer.id, vehicle.id
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Select or create a customer")

    if payload.vehicle_id:
        vehicle = await active_record(session, models.CustomerVehicle, payload.vehicle_id, "Vehicle")
        if vehicle.customer_id != customer_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Selected vehicle does not belong to the selected customer.",
            )
        return customer_id, vehicle.id
    if payload.new_vehicle:
        if not payload.new_vehicle.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New vehicles on a job card must be active.",
            )
        vehicle_data = payload.new_vehicle.model_dump()
        vehicle_data["customer_id"] = customer_id
        vehicle_data["registration"] = vehicle_data["registration"].upper()
        vehicle = models.CustomerVehicle(**vehicle_data)
        session.add(vehicle)
        await session.flush()
        return customer_id, vehicle.id

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Select or create a vehicle")


async def latest_invoice_for_job(
    session: AsyncSession,
    job_card_id: int,
) -> models.Invoice | None:
    return await session.scalar(
        select(models.Invoice)
        .where(models.Invoice.job_card_id == job_card_id, models.Invoice.is_active.is_(True))
        .order_by(models.Invoice.id.desc())
        .limit(1)
    )


async def job_card_summary_row(session: AsyncSession, job_card: models.JobCard) -> dict:
    customer = await session.get(models.Customer, job_card.customer_id)
    vehicle = await session.get(models.CustomerVehicle, job_card.vehicle_id)
    invoice = await latest_invoice_for_job(session, job_card.id)
    return {
        "id": job_card.id,
        "job_number": job_card.job_number,
        "customer_id": job_card.customer_id,
        "vehicle_id": job_card.vehicle_id,
        "status": job_card.status,
        "intake_mode": job_card.intake_mode,
        "delivery_mode": job_card.delivery_mode,
        "fuel_level": job_card.fuel_level,
        "mileage": job_card.mileage,
        "subtotal": job_card.subtotal,
        "discount_amount": job_card.discount_amount,
        "tax_rate": job_card.tax_rate,
        "total_amount": job_card.total_amount,
        "is_active": job_card.is_active,
        "created_at": job_card.created_at,
        "updated_at": job_card.updated_at,
        "customer_name": customer.full_name if customer else None,
        "customer_phone": customer.phone if customer else None,
        "customer_email": customer.email if customer else None,
        "vehicle_registration": vehicle.registration if vehicle else None,
        "vehicle_make": vehicle.make if vehicle else None,
        "vehicle_type": vehicle.vehicle_type if vehicle else None,
        "invoice_number": invoice.invoice_number if invoice else None,
        "invoice_total": invoice.total_amount if invoice else None,
    }


async def live_invoice_lines(
    session: AsyncSession,
    job_card_id: int,
    invoice: models.Invoice | None = None,
) -> list[dict]:
    rows: list[dict] = []
    works = (
        await session.scalars(
            select(models.JobCardWork).where(
                models.JobCardWork.job_card_id == job_card_id,
                models.JobCardWork.is_active.is_(True),
            )
        )
    ).all()
    for work in works:
        work_type = await session.get(models.WorkType, work.work_type_id)
        rows.append(
            {
                "source": "work",
                "source_id": work.id,
                "item": "Service",
                "description": work_type.name if work_type else "Labour",
                "quantity": Decimal("1.00"),
                "rate": money(work.labour_amount),
                "amount": money(work.labour_amount),
            }
        )

    items = (
        await session.scalars(
            select(models.JobCardItem).where(
                models.JobCardItem.job_card_id == job_card_id,
                models.JobCardItem.is_active.is_(True),
            )
        )
    ).all()
    for item in items:
        garage_item = await session.get(models.GarageItem, item.garage_item_id)
        rows.append(
            {
                "source": "item",
                "source_id": item.id,
                "item": "Part",
                "description": garage_item.name if garage_item else "Garage item",
                "quantity": item.quantity,
                "rate": item.unit_price,
                "amount": item.amount,
            }
        )

    costs = (
        await session.scalars(
            select(models.JobCardCost).where(
                models.JobCardCost.job_card_id == job_card_id,
                models.JobCardCost.is_active.is_(True),
            )
        )
    ).all()
    for cost in costs:
        rows.append(
            {
                "source": "cost",
                "source_id": cost.id,
                "item": "Cost",
                "description": cost.label,
                "quantity": Decimal("1.00"),
                "rate": cost.amount,
                "amount": cost.amount,
            }
        )

    subtotal = sum((money(row["amount"]) for row in rows), Decimal("0.00"))
    if invoice:
        discount = money(invoice.discount_amount)
        tax_rate = money(invoice.tax_rate)
        tax = money(invoice.vat_amount)
        tax_source_id = invoice.id
    else:
        job_card = await session.get(models.JobCard, job_card_id)
        discount = money(job_card.discount_amount if job_card else 0)
        tax_rate = money(job_card.tax_rate if job_card else 0)
        tax = money(max(Decimal("0.00"), subtotal - discount) * tax_rate / Decimal("100"))
        tax_source_id = None

    if discount > 0:
        rows.append(
            {
                "source": "discount",
                "source_id": invoice.id if invoice else None,
                "item": "Discount",
                "description": "Discount",
                "quantity": Decimal("1.00"),
                "rate": -discount,
                "amount": -discount,
            }
        )
    if tax > 0:
        rate_label = f" ({tax_rate:g}%)" if tax_rate > 0 else ""
        rows.append(
            {
                "source": "tax",
                "source_id": tax_source_id,
                "item": "Tax",
                "description": f"Tax{rate_label}",
                "quantity": Decimal("1.00"),
                "rate": tax,
                "amount": tax,
            }
        )
    return rows


async def stored_invoice_lines(session: AsyncSession, invoice_id: int) -> list[dict]:
    lines = (
        await session.scalars(
            select(models.InvoiceLine)
            .where(models.InvoiceLine.invoice_id == invoice_id)
            .order_by(models.InvoiceLine.line_order.asc(), models.InvoiceLine.id.asc())
        )
    ).all()
    return [
        {
            "id": line.id,
            "invoice_id": line.invoice_id,
            "line_order": line.line_order,
            "source": line.source,
            "source_id": line.source_id,
            "item": line.item,
            "description": line.description,
            "quantity": line.quantity,
            "rate": line.rate,
            "amount": line.amount,
        }
        for line in lines
    ]


async def snapshot_invoice_lines(
    session: AsyncSession,
    invoice_id: int,
    lines: Sequence[dict],
) -> None:
    materialized_lines = list(lines) or [
        {
            "source": "empty",
            "source_id": invoice_id,
            "item": "Invoice",
            "description": "No charges",
            "quantity": Decimal("1.00"),
            "rate": Decimal("0.00"),
            "amount": Decimal("0.00"),
        }
    ]
    for index, line in enumerate(materialized_lines, start=1):
        session.add(
            models.InvoiceLine(
                invoice_id=invoice_id,
                line_order=index,
                source=str(line.get("source") or "line"),
                source_id=line.get("source_id"),
                item=str(line.get("item") or "Line"),
                description=str(line.get("description") or ""),
                quantity=money(line.get("quantity") or 1),
                rate=money(line.get("rate") or 0),
                amount=money(line.get("amount") or 0),
            )
        )


async def invoice_lines(
    session: AsyncSession,
    job_card_id: int,
    invoice: models.Invoice | None = None,
) -> list[dict]:
    if invoice:
        snapshot = await stored_invoice_lines(session, invoice.id)
        if snapshot:
            return snapshot
    return await live_invoice_lines(session, job_card_id, invoice)


async def invoice_context(session: AsyncSession, invoice_id: int) -> dict:
    invoice = await session.get(models.Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    job_card = await session.get(models.JobCard, invoice.job_card_id)
    customer = await session.get(models.Customer, job_card.customer_id)
    vehicle = await session.get(models.CustomerVehicle, job_card.vehicle_id)
    return {
        "invoice": invoice,
        "job_card": job_card,
        "customer": customer,
        "vehicle": vehicle,
        "lines": await invoice_lines(session, job_card.id, invoice),
    }


async def job_card_detail(session: AsyncSession, job_card_id: int) -> dict:
    job_card = await session.get(models.JobCard, job_card_id)
    if not job_card:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job card not found")
    customer = await session.get(models.Customer, job_card.customer_id)
    vehicle = await session.get(models.CustomerVehicle, job_card.vehicle_id)
    invoice = await latest_invoice_for_job(session, job_card.id)
    return {
        "job_card": await job_card_summary_row(session, job_card),
        "customer": {
            "name": customer.full_name,
            "phone": customer.phone,
            "email": customer.email,
            "county": customer.county,
            "location_description": customer.location_description,
        }
        if customer
        else None,
        "vehicle": {
            "registration": vehicle.registration,
            "make": vehicle.make,
            "type": vehicle.vehicle_type,
            "year": vehicle.year,
            "description": vehicle.description,
        }
        if vehicle
        else None,
        "invoice": {
            "id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "invoice_type": invoice.invoice_type,
            "subtotal": invoice.subtotal,
            "discount_amount": invoice.discount_amount,
            "vat_amount": invoice.vat_amount,
            "tax_rate": invoice.tax_rate,
            "total_amount": invoice.total_amount,
            "payment_status": invoice.payment_status,
            "amount_paid": invoice.amount_paid,
            "balance_due": invoice.balance_due,
            "created_at": invoice.created_at,
            "updated_at": invoice.updated_at,
        }
        if invoice
        else None,
        "lines": await live_invoice_lines(session, job_card.id),
        "intake": {
            "intake_mode": job_card.intake_mode,
            "delivery_mode": job_card.delivery_mode,
            "fuel_level": job_card.fuel_level,
            "mileage": job_card.mileage,
            "present_items": job_card.present_items,
            "other_present_items": job_card.other_present_items,
            "condition_notes": job_card.condition_notes,
            "invoice_notes": job_card.invoice_notes,
        },
    }


# NOTE: ``/job-cards/stats`` and ``/job-cards/recent`` must be declared
# BEFORE the ``/job-cards/{job_card_id}`` family. FastAPI matches in
# declaration order; a dynamic ``{job_card_id}`` route registered first
# would swallow ``stats`` and try to parse it as an int.


@app.get("/job-cards/stats")
async def job_card_stats(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
) -> dict:
    """Lightweight aggregate used by the dashboard so it doesn't have to
    fetch every job card just to show counts. Returns counts grouped by
    status plus an overall total and a 'pending' rollup that excludes the
    terminal closed/cancelled buckets."""
    rows = (
        await session.execute(
            select(models.JobCard.status, func.count(models.JobCard.id))
            .group_by(models.JobCard.status)
        )
    ).all()
    by_status = {
        (status_value.value if hasattr(status_value, "value") else str(status_value)): count
        for status_value, count in rows
    }
    pending_statuses = {"draft", "in_progress", "ready", "invoiced", "complete"}
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "pending": sum(count for key, count in by_status.items() if key in pending_statuses),
    }


@app.get("/job-cards/recent")
async def recent_job_cards(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
    limit: int = 20,
    status_filter: str | None = None,
) -> list[dict]:
    """Most-recent N cards, optionally filtered by status. Server-side
    filter + limit so the dashboard never has to pull the full history."""
    stmt = select(models.JobCard).order_by(models.JobCard.id.desc())
    if status_filter:
        wanted = {part.strip() for part in status_filter.split(",") if part.strip()}
        try:
            statuses = [models.JobCardStatus(value) for value in wanted]
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown status: {exc}",
            ) from exc
        stmt = stmt.where(models.JobCard.status.in_(statuses))
    stmt = stmt.limit(min(max(1, limit), 200))
    job_cards = (await session.scalars(stmt)).all()
    return [await job_card_summary_row(session, jc) for jc in job_cards]


@app.get("/job-cards/{job_card_id}/lines")
async def get_job_card_lines(
    job_card_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
):
    job_card = await session.get(models.JobCard, job_card_id)
    if not job_card:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job card not found")
    return await live_invoice_lines(session, job_card.id)


@app.get("/job-cards/{job_card_id}")
async def get_job_card_detail(
    job_card_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
):
    return await job_card_detail(session, job_card_id)


@app.patch("/job-cards/{job_card_id}/status")
async def update_job_card_status(
    job_card_id: int,
    payload: schemas.JobCardStatusUpdate,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    job_card = await session.get(models.JobCard, job_card_id)
    if not job_card:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job card not found")

    previous_status = job_card.status
    target_status = payload.status

    # Cancelling a closed card would silently re-credit stock that was already
    # billed and paid for. Block it — admin should clone the card if they need
    # a fresh start.
    if (
        target_status == models.JobCardStatus.cancelled
        and previous_status == models.JobCardStatus.closed
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Closed job cards cannot be cancelled. Open a new card instead.",
        )

    invoice = await latest_invoice_for_job(session, job_card.id)
    if target_status == models.JobCardStatus.closed:
        await recompute_job_card_totals(session, job_card.id)
        if not invoice:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment has to be complete before closing this job card.",
            )
        if invoice.invoice_type != models.InvoiceType.final:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Create a final invoice before closing this job card.",
            )
        if money(invoice.total_amount) != money(job_card.total_amount):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Create the latest invoice before closing this job card.",
            )
        if money(invoice.balance_due) > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment has to be complete before closing this job card.",
            )

    refunded_movements = 0
    if (
        target_status == models.JobCardStatus.cancelled
        and previous_status != models.JobCardStatus.cancelled
    ):
        # Idempotent — only stock_outs without a matching stock_in get reversed.
        refunded_movements = await refund_stock_for_job(session, job_card.id)

    job_card.status = target_status
    await session.commit()
    await session.refresh(job_card)

    email_queued = False
    if (
        previous_status != models.JobCardStatus.complete
        and target_status == models.JobCardStatus.complete
    ):
        customer = await session.get(models.Customer, job_card.customer_id)
        vehicle = await session.get(models.CustomerVehicle, job_card.vehicle_id)
        if customer and customer.email and vehicle:
            background_tasks.add_task(
                send_job_complete_email,
                customer.email,
                customer.full_name,
                vehicle.registration,
                job_card.job_number,
            )
            email_queued = True

    return {
        "job_card": await job_card_summary_row(session, job_card),
        "email_queued": email_queued,
        "refunded_movements": refunded_movements,
        "message": "Job card status updated.",
    }


# --------------------------------------------------------------------------- #
# Job-card editing — header + line management
# --------------------------------------------------------------------------- #


_LOCKED_JOB_STATUSES = {models.JobCardStatus.closed, models.JobCardStatus.cancelled}


async def _ensure_job_card_editable(session: AsyncSession, job_card_id: int) -> models.JobCard:
    job_card = await session.get(models.JobCard, job_card_id)
    if not job_card:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job card not found")
    if job_card.status in _LOCKED_JOB_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Job card is {job_card.status.value}; reopen it before adding or "
                "changing line items."
            ),
        )
    # Block edits once the bill is final — the invoice is the contract.
    finalised = await session.scalar(
        select(models.Invoice.id)
        .where(
            models.Invoice.job_card_id == job_card_id,
            models.Invoice.invoice_type == models.InvoiceType.final,
            models.Invoice.is_active.is_(True),
        )
        .limit(1)
    )
    if finalised:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This job card's invoice has been finalised — line items are "
                "locked. Open a new job card if more work is needed."
            ),
        )
    return job_card


async def latest_active_invoice_for_job(
    session: AsyncSession, job_card_id: int
) -> models.Invoice | None:
    """Return the single active invoice on this job card, if any."""
    return await session.scalar(
        select(models.Invoice)
        .where(
            models.Invoice.job_card_id == job_card_id,
            models.Invoice.is_active.is_(True),
        )
        .order_by(models.Invoice.id.desc())
        .limit(1)
    )


async def recompute_job_card_totals(session: AsyncSession, job_card_id: int) -> None:
    """Rebuild current job-card totals from the active work, item, and cost lines.

    Also syncs the live **intermediate** invoice (if one exists) so the
    customer-facing estimate stays in step with line-item edits. Once an
    invoice has been finalized, its totals are locked — only the job card's
    own ``subtotal`` / ``total_amount`` continue to track (historical interest).
    """
    works_total = await session.scalar(
        select(func.coalesce(func.sum(models.JobCardWork.labour_amount), 0)).where(
            models.JobCardWork.job_card_id == job_card_id,
            models.JobCardWork.is_active.is_(True),
        )
    )
    items_total = await session.scalar(
        select(func.coalesce(func.sum(models.JobCardItem.amount), 0)).where(
            models.JobCardItem.job_card_id == job_card_id,
            models.JobCardItem.is_active.is_(True),
        )
    )
    costs_total = await session.scalar(
        select(func.coalesce(func.sum(models.JobCardCost.amount), 0)).where(
            models.JobCardCost.job_card_id == job_card_id,
            models.JobCardCost.is_active.is_(True),
        )
    )

    job_card = await session.get(models.JobCard, job_card_id)
    if not job_card:
        return
    subtotal = money(works_total) + money(items_total) + money(costs_total)
    job_card.subtotal = subtotal

    discount = money(
        job_card.discount_amount if job_card.discount_amount is not None else 0
    )
    tax_rate = money(job_card.tax_rate)
    net = max(Decimal("0.00"), subtotal - discount)
    tax_amount = money(net * tax_rate / Decimal("100"))
    total_amount = money(net + tax_amount)
    job_card.total_amount = total_amount

    # Keep the live intermediate invoice in sync so customers always see the
    # current state of their estimate. Finals stay locked.
    intermediate = await session.scalar(
        select(models.Invoice)
        .where(
            models.Invoice.job_card_id == job_card_id,
            models.Invoice.invoice_type == models.InvoiceType.intermediate,
            models.Invoice.is_active.is_(True),
        )
        .order_by(models.Invoice.id.desc())
        .limit(1)
    )
    if intermediate:
        intermediate.subtotal = subtotal
        intermediate.discount_amount = discount
        intermediate.tax_rate = tax_rate
        intermediate.vat_amount = tax_amount
        intermediate.total_amount = total_amount


async def upsert_intermediate_invoice(
    session: AsyncSession,
    job_card: models.JobCard,
    notes: str | None,
) -> models.Invoice:
    """Idempotent: either updates the existing intermediate or creates one.

    This is the single-source-of-truth invoice for the card — same invoice
    number across every call, only the totals + notes shift as line items
    evolve. Refuses with 409 if the job card has already been finalised.
    Intermediate invoices live-compute their line items via the
    ``invoice_lines`` dispatcher; no snapshot is written until ``finalize_invoice``.
    """
    existing = await latest_active_invoice_for_job(session, job_card.id)
    if existing and existing.invoice_type == models.InvoiceType.final:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This job card has already been finalised. Open a new job card "
                "if more work needs to be billed."
            ),
        )

    # Refreshes job-card totals *and* the existing intermediate invoice.
    await recompute_job_card_totals(session, job_card.id)

    if existing:
        if notes is not None:
            existing.notes = notes
        return existing

    net_amount = max(
        Decimal("0.00"), money(job_card.subtotal) - money(job_card.discount_amount)
    )
    tax_amount = money(net_amount * money(job_card.tax_rate) / Decimal("100"))
    invoice = models.Invoice(
        invoice_number=await next_number(session, models.Invoice, "INV"),
        job_card_id=job_card.id,
        invoice_type=models.InvoiceType.intermediate,
        subtotal=money(job_card.subtotal),
        vat_amount=tax_amount,
        tax_rate=money(job_card.tax_rate),
        discount_amount=money(job_card.discount_amount),
        total_amount=money(net_amount + tax_amount),
        notes=notes,
        payment_status=models.PaymentStatus.not_paid,
        amount_paid=Decimal("0.00"),
        is_active=True,
    )
    sync_invoice_payment_status(invoice)
    session.add(invoice)
    await session.flush()
    # No snapshot for intermediate — lines stay live-computed via
    # ``invoice_lines`` so each share with the customer reflects the
    # current state of the job card.
    return invoice


async def finalize_invoice(
    session: AsyncSession,
    invoice: models.Invoice,
) -> models.Invoice:
    """Promote the job card's intermediate invoice to **final**.

    Refreshes totals one last time, snapshots the line items into the
    ``invoice_lines`` table (so the bill becomes immutable), flips the type,
    and moves the job card to ``invoiced`` status if it isn't already past
    that. After this, ``_ensure_job_card_editable`` refuses further line
    edits on the card — the bill is the bill.
    """
    if invoice.invoice_type == models.InvoiceType.final:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invoice has already been finalised.",
        )

    job_card = await session.get(models.JobCard, invoice.job_card_id)
    if not job_card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job card not found"
        )

    # One last refresh so any last-minute line edits land before we freeze.
    await recompute_job_card_totals(session, job_card.id)
    net_amount = max(
        Decimal("0.00"), money(job_card.subtotal) - money(job_card.discount_amount)
    )
    tax_amount = money(net_amount * money(job_card.tax_rate) / Decimal("100"))
    invoice.subtotal = money(job_card.subtotal)
    invoice.discount_amount = money(job_card.discount_amount)
    invoice.tax_rate = money(job_card.tax_rate)
    invoice.vat_amount = tax_amount
    invoice.total_amount = money(net_amount + tax_amount)
    invoice.invoice_type = models.InvoiceType.final

    # Lock the lines as a snapshot — future card edits won't change the bill.
    await snapshot_invoice_lines(
        session,
        invoice.id,
        await live_invoice_lines(session, job_card.id, invoice),
    )

    if job_card.status not in {
        models.JobCardStatus.complete,
        models.JobCardStatus.closed,
        models.JobCardStatus.cancelled,
    }:
        job_card.status = models.JobCardStatus.invoiced
    return invoice


@app.patch("/job-cards/{job_card_id}", response_model=schemas.JobCardRead)
async def update_job_card_header(
    job_card_id: int,
    payload: schemas.JobCardHeaderUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    """Update non-line job-card fields (fuel, mileage, intake/return mode,
    pickup/dropoff cost, notes, discount, tax-rate).

    Locked once the card is closed or cancelled. Recomputes totals if the
    discount or tax rate moved.
    """
    job_card = await _ensure_job_card_editable(session, job_card_id)
    updates = payload.model_dump(exclude_unset=True)
    tax_rate = updates.pop("tax_rate", None)
    present_items = updates.pop("present_items", None)
    await optional_active_record(session, models.User, updates.get("picked_up_by_user_id"), "Pickup user")
    await optional_active_record(session, models.GarageCost, updates.get("pickup_cost_id"), "Pickup cost")
    await optional_active_record(session, models.GarageCost, updates.get("dropoff_cost_id"), "Dropoff cost")
    if present_items is not None:
        job_card.present_items = ", ".join(present_items)

    money_fields = {
        "pickup_cost_amount",
        "dropoff_cost_amount",
        "discount_amount",
    }
    for key, value in updates.items():
        if key in money_fields and value is not None:
            value = money(value)
        setattr(job_card, key, value)

    if tax_rate is not None:
        job_card.tax_rate = money(tax_rate)

    await recompute_job_card_totals(session, job_card_id)
    await session.commit()
    await session.refresh(job_card)
    return await job_card_summary_row(session, job_card)


@app.post("/job-cards/{job_card_id}/works", response_model=schemas.JobCardRead)
async def add_job_card_work(
    job_card_id: int,
    payload: schemas.JobCardWorkInput,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    job_card = await _ensure_job_card_editable(session, job_card_id)
    await active_record(session, models.WorkType, payload.work_type_id, "Work type")
    await optional_active_record(session, models.Worker, payload.worker_id, "Worker")
    session.add(
        models.JobCardWork(
            job_card_id=job_card.id,
            work_type_id=payload.work_type_id,
            worker_id=payload.worker_id,
            notes=payload.notes,
            labour_amount=money(payload.labour_amount),
            is_active=True,
        )
    )
    await session.flush()
    await recompute_job_card_totals(session, job_card_id)
    await session.commit()
    await session.refresh(job_card)
    return await job_card_summary_row(session, job_card)


@app.delete("/job-cards/{job_card_id}/works/{work_id}", response_model=schemas.JobCardRead)
async def remove_job_card_work(
    job_card_id: int,
    work_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    job_card = await _ensure_job_card_editable(session, job_card_id)
    work = await session.get(models.JobCardWork, work_id)
    if not work or work.job_card_id != job_card_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work line not found")
    work.is_active = False  # soft-delete keeps the audit trail
    await recompute_job_card_totals(session, job_card_id)
    await session.commit()
    await session.refresh(job_card)
    return await job_card_summary_row(session, job_card)


@app.post("/job-cards/{job_card_id}/items", response_model=schemas.JobCardRead)
async def add_job_card_item(
    job_card_id: int,
    payload: schemas.JobCardInventoryItemInput,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    """Add a part to an existing job card. Consumes stock FIFO."""
    job_card = await _ensure_job_card_editable(session, job_card_id)
    await active_record(session, models.GarageItem, payload.garage_item_id, "Garage item")
    await consume_stock(session, job_card.id, payload)
    await recompute_job_card_totals(session, job_card_id)
    await session.commit()
    await session.refresh(job_card)
    return await job_card_summary_row(session, job_card)


@app.delete("/job-cards/{job_card_id}/items/{item_id}", response_model=schemas.JobCardRead)
async def remove_job_card_item(
    job_card_id: int,
    item_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    """Remove a part line; the stock it consumed is refunded automatically."""
    job_card = await _ensure_job_card_editable(session, job_card_id)
    item = await session.get(models.JobCardItem, item_id)
    if not item or item.job_card_id != job_card_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item line not found")

    from app.services.inventory import refund_stock_for_job_item

    refunded = await refund_stock_for_job_item(
        session,
        job_card_id=job_card_id,
        garage_item_id=item.garage_item_id,
        quantity_to_refund=item.quantity,
    )
    item.is_active = False  # soft-delete preserves history
    await recompute_job_card_totals(session, job_card_id)
    await session.commit()
    await session.refresh(job_card)
    return {**(await job_card_summary_row(session, job_card)), "refunded_quantity": str(refunded)}


@app.post("/job-cards/{job_card_id}/costs", response_model=schemas.JobCardRead)
async def add_job_card_cost(
    job_card_id: int,
    payload: schemas.JobCardCostInput,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    job_card = await _ensure_job_card_editable(session, job_card_id)
    await optional_active_record(session, models.GarageCost, payload.garage_cost_id, "Garage cost")
    session.add(
        models.JobCardCost(
            job_card_id=job_card.id,
            garage_cost_id=payload.garage_cost_id,
            label=payload.label,
            amount=money(payload.amount),
            cost_type=payload.cost_type,
            is_active=True,
        )
    )
    await session.flush()
    await recompute_job_card_totals(session, job_card_id)
    await session.commit()
    await session.refresh(job_card)
    return await job_card_summary_row(session, job_card)


@app.delete("/job-cards/{job_card_id}/costs/{cost_id}", response_model=schemas.JobCardRead)
async def remove_job_card_cost(
    job_card_id: int,
    cost_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    job_card = await _ensure_job_card_editable(session, job_card_id)
    cost = await session.get(models.JobCardCost, cost_id)
    if not cost or cost.job_card_id != job_card_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cost line not found")
    cost.is_active = False
    await recompute_job_card_totals(session, job_card_id)
    await session.commit()
    await session.refresh(job_card)
    return await job_card_summary_row(session, job_card)


@app.get("/job-cards", response_model=list[schemas.JobCardRead])
async def get_job_cards(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
    status_filter: str | None = None,
    limit: int | None = None,
):
    """Listing with optional ``?status=`` (comma-separated) + ``?limit=`` so
    the dashboard can pull just the pending slice it actually renders.

    Without filters this still returns every card — kept for backwards
    compatibility with callers that page in the client."""
    stmt = select(models.JobCard).order_by(models.JobCard.id.desc())
    if status_filter:
        wanted = {
            part.strip()
            for part in status_filter.split(",")
            if part.strip()
        }
        try:
            statuses = [models.JobCardStatus(value) for value in wanted]
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown status: {exc}",
            ) from exc
        stmt = stmt.where(models.JobCard.status.in_(statuses))
    if limit:
        # Clamp to a sane ceiling so a typo doesn't accidentally pull the world.
        stmt = stmt.limit(min(max(1, limit), 500))
    result = await session.scalars(stmt)
    job_cards = result.all()
    return [await job_card_summary_row(session, job_card) for job_card in job_cards]


@app.post("/job-cards", response_model=schemas.InvoiceRead)
async def create_job_card(
    payload: schemas.JobCardCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    customer_id, vehicle_id = await resolve_job_customer_vehicle(session, payload)
    await optional_active_record(session, models.User, payload.picked_up_by_user_id, "Pickup user")
    await optional_active_record(session, models.GarageCost, payload.pickup_cost_id, "Pickup cost")
    await optional_active_record(session, models.GarageCost, payload.dropoff_cost_id, "Dropoff cost")
    for work in payload.works:
        await active_record(session, models.WorkType, work.work_type_id, "Work type")
        await optional_active_record(session, models.Worker, work.worker_id, "Worker")
    for item in payload.inventory_items:
        await active_record(session, models.GarageItem, item.garage_item_id, "Garage item")
    for cost in payload.additional_costs:
        await optional_active_record(session, models.GarageCost, cost.garage_cost_id, "Garage cost")

    job_card = models.JobCard(
        job_number=await next_number(session, models.JobCard, "JC"),
        customer_id=customer_id,
        vehicle_id=vehicle_id,
        status=models.JobCardStatus.in_progress,
        intake_mode=payload.intake_mode,
        picked_up_by_user_id=payload.picked_up_by_user_id,
        pickup_cost_id=payload.pickup_cost_id,
        pickup_cost_amount=money(payload.pickup_cost_amount),
        delivery_mode=payload.delivery_mode,
        dropoff_cost_id=payload.dropoff_cost_id,
        dropoff_cost_amount=money(payload.dropoff_cost_amount),
        fuel_level=payload.fuel_level,
        mileage=payload.mileage,
        present_items=", ".join(payload.present_items),
        other_present_items=payload.other_present_items,
        condition_notes=payload.condition_notes,
        invoice_notes=payload.invoice_notes,
        subtotal=Decimal("0.00"),
        discount_amount=money(payload.discount_amount),
        tax_rate=money(payload.tax_rate),
        total_amount=Decimal("0.00"),
        is_active=True,
    )
    session.add(job_card)
    await session.flush()

    subtotal = Decimal("0.00")
    for work in payload.works:
        session.add(models.JobCardWork(job_card_id=job_card.id, **work.model_dump(), is_active=True))
        subtotal += money(work.labour_amount)

    for item in payload.inventory_items:
        job_item = await consume_stock(session, job_card.id, item)
        subtotal += money(job_item.amount)

    cost_rows = list(payload.additional_costs)
    if payload.pickup_cost_id and payload.pickup_cost_amount:
        cost = await session.get(models.GarageCost, payload.pickup_cost_id)
        cost_rows.append(
            schemas.JobCardCostInput(
                garage_cost_id=payload.pickup_cost_id,
                label=cost.name if cost else "Pickup cost",
                amount=payload.pickup_cost_amount,
                cost_type="pickup",
            )
        )
    if payload.dropoff_cost_id and payload.dropoff_cost_amount:
        cost = await session.get(models.GarageCost, payload.dropoff_cost_id)
        cost_rows.append(
            schemas.JobCardCostInput(
                garage_cost_id=payload.dropoff_cost_id,
                label=cost.name if cost else "Dropoff cost",
                amount=payload.dropoff_cost_amount,
                cost_type="dropoff",
            )
        )
    for cost in cost_rows:
        session.add(models.JobCardCost(job_card_id=job_card.id, **cost.model_dump(), is_active=True))
        subtotal += money(cost.amount)

    job_card.subtotal = money(subtotal)
    # A new card always starts with an intermediate (estimate) invoice — the
    # legacy ``payload.invoice_type`` field is ignored. Promote to final via
    # ``POST /invoices/{id}/finalize`` when the customer is ready to be billed.
    invoice = await upsert_intermediate_invoice(
        session,
        job_card,
        payload.invoice_notes,
    )
    await session.commit()
    await session.refresh(invoice)
    return invoice


@app.post("/job-cards/{job_card_id}/invoices", response_model=schemas.InvoiceRead)
async def upsert_intermediate_invoice_endpoint(
    job_card_id: int,
    payload: schemas.JobCardInvoiceCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    """Create or refresh the **intermediate** invoice for this job card.

    Idempotent — the same invoice number is returned across calls. Use this
    to keep the customer-facing estimate in step as line items evolve, then
    call ``POST /invoices/{id}/finalize`` when the bill is ready.

    The ``invoice_type`` field on the payload is ignored — new invoices are
    always created as intermediate. Type only changes through ``finalize``.
    """
    job_card = await _ensure_job_card_editable(session, job_card_id)
    notes = payload.notes if payload.notes is not None else job_card.invoice_notes
    invoice = await upsert_intermediate_invoice(session, job_card, notes)
    await session.commit()
    await session.refresh(invoice)
    return invoice


@app.post("/invoices/{invoice_id}/finalize", response_model=schemas.InvoiceRead)
async def finalize_invoice_endpoint(
    invoice_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    """Promote a job card's intermediate invoice to **final**.

    Snapshots the line items so the bill becomes immutable, locks the
    job card against further edits, moves the card to ``invoiced``, and
    unlocks the payment-recording flow. The same invoice number is kept.
    """
    invoice = await session.get(models.Invoice, invoice_id)
    if not invoice or not invoice.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    invoice = await finalize_invoice(session, invoice)
    await session.commit()
    await session.refresh(invoice)
    return invoice


@app.get("/invoices", response_model=list[schemas.InvoiceRead])
async def get_invoices(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
):
    return await list_records(session, models.Invoice)


@app.get("/invoices/{invoice_id}/lines")
async def get_invoice_lines(
    invoice_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
):
    context = await invoice_context(session, invoice_id)
    return context["lines"]


@app.patch("/invoices/{invoice_id}/payment", response_model=schemas.InvoiceRead)
async def update_invoice_payment(
    invoice_id: int,
    payload: schemas.InvoicePaymentUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    """Record an invoice payment.

    Intermediate invoices are estimates and can't be paid against — only
    final invoices are eligible for receivables. ``payment_status`` is
    always derived from ``amount_paid`` versus the invoice total so the two
    cannot drift apart.
    """
    invoice = await session.get(models.Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if invoice.invoice_type != models.InvoiceType.final:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Payments can only be recorded against a final invoice — this "
                "is an intermediate estimate. Issue the final invoice from the "
                "job card first, then record payment there."
            ),
        )
    amount_paid = money(payload.amount_paid)
    total = money(invoice.total_amount)
    if amount_paid > total:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Amount paid cannot be more than invoice total",
        )
    invoice.amount_paid = amount_paid
    sync_invoice_payment_status(invoice)
    await session.commit()
    await session.refresh(invoice)
    return invoice


@app.get("/invoices/{invoice_id}/pdf")
async def invoice_pdf(
    invoice_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(get_current_user)],
):
    pdf_bytes, filename = build_invoice_pdf(await invoice_context(session, invoice_id))
    return Response(
        pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/invoices/{invoice_id}/share-email", response_model=schemas.InvoiceShareRead)
async def share_invoice_email(
    invoice_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    context = await invoice_context(session, invoice_id)
    invoice = context["invoice"]
    customer = context["customer"]
    vehicle = context["vehicle"]
    if not customer.email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Customer has no email")
    body = (
        f"Hello {customer.full_name},\n\n"
        f"Please find invoice {invoice.invoice_number} for vehicle {vehicle.registration}.\n"
        f"Total: KES {invoice.total_amount:,.2f}\n\n"
        "Regards,\nJob Card Automation System"
    )
    pdf_bytes, filename = build_invoice_pdf(context)
    emailed = send_email_with_attachment(
        customer.email,
        f"Invoice {invoice.invoice_number}",
        body,
        pdf_bytes,
        filename,
    )
    if emailed:
        invoice.is_sent_email = True
        await session.commit()
    return schemas.InvoiceShareRead(invoice_id=invoice.id, emailed=emailed, message=body)


@app.post("/invoices/{invoice_id}/share-whatsapp", response_model=schemas.InvoiceShareRead)
async def share_invoice_whatsapp(
    invoice_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[models.User, Depends(require_admin)],
):
    context = await invoice_context(session, invoice_id)
    invoice = context["invoice"]
    customer = context["customer"]
    vehicle = context["vehicle"]
    phone = "".join(char for char in customer.phone if char.isdigit())
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    message = (
        f"Hello {customer.full_name}, invoice {invoice.invoice_number} for "
        f"{vehicle.registration} is KES {invoice.total_amount:,.2f}."
    )
    invoice.is_sent_whatsapp = True
    await session.commit()
    return schemas.InvoiceShareRead(
        invoice_id=invoice.id,
        whatsapp_url=f"https://wa.me/{phone}?text={quote(message)}",
        message=message,
    )


KENYA_COUNTIES = [
    "In shop",
    "Baringo",
    "Bomet",
    "Bungoma",
    "Busia",
    "Elgeyo Marakwet",
    "Embu",
    "Garissa",
    "Homa Bay",
    "Isiolo",
    "Kajiado",
    "Kakamega",
    "Kericho",
    "Kiambu",
    "Kilifi",
    "Kirinyaga",
    "Kisii",
    "Kisumu",
    "Kitui",
    "Kwale",
    "Laikipia",
    "Lamu",
    "Machakos",
    "Makueni",
    "Mandera",
    "Marsabit",
    "Meru",
    "Migori",
    "Mombasa",
    "Murang'a",
    "Nairobi",
    "Nakuru",
    "Nandi",
    "Narok",
    "Nyamira",
    "Nyandarua",
    "Nyeri",
    "Samburu",
    "Siaya",
    "Taita Taveta",
    "Tana River",
    "Tharaka Nithi",
    "Trans Nzoia",
    "Turkana",
    "Uasin Gishu",
    "Vihiga",
    "Wajir",
    "West Pokot",
]

CAR_MODELS = [
    "Toyota",
    "Mazda",
    "Nissan",
    "Subaru",
    "Mitsubishi",
    "Isuzu",
    "Mercedes-Benz",
    "BMW",
    "Volkswagen",
    "Honda",
    "Suzuki",
    "Hyundai",
    "Kia",
    "Ford",
    "Other",
]
