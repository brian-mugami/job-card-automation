"""Password hashing + HMAC-signed token helpers.

PBKDF2 iteration counts evolve over time (OWASP currently recommends 600k for
SHA-256 as of 2023). To avoid forcing every existing user to reset their
password whenever we bump the cost, the iteration count is encoded inside the
hash and ``verify_password`` honours whatever count it finds there.

Tokens embed a ``pwd_at`` claim that holds the user's ``password_changed_at``
epoch seconds at the moment the token was issued. ``decode_token`` returns
that claim untouched; ``get_current_user`` compares it against the user's
current ``password_changed_at`` and rejects stale tokens. That gives us a
cheap session-invalidation primitive when an admin resets their password.
"""
import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings

# OWASP 2023 recommendation for PBKDF2-SHA256.
PBKDF2_ITERATIONS = 600_000
PBKDF2_ALGORITHM = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    """Produce a ``pbkdf2_sha256$<iterations>$<salt>$<hex digest>`` string."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS
    )
    return f"{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify against a hash from any historical iteration count.

    Supports both the legacy ``pbkdf2_sha256$<salt>$<hex>`` (3 parts, 120k
    iterations) format and the new ``pbkdf2_sha256$<iterations>$<salt>$<hex>``
    (4 parts) format. ``hmac.compare_digest`` for timing-safe comparison.
    """
    parts = password_hash.split("$")
    try:
        if len(parts) == 4:
            algorithm, iterations_str, salt, expected = parts
            iterations = int(iterations_str)
        elif len(parts) == 3:
            algorithm, salt, expected = parts
            iterations = 120_000  # legacy default
        else:
            return False
    except (ValueError, TypeError):
        return False

    if algorithm != PBKDF2_ALGORITHM:
        return False

    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return hmac.compare_digest(digest.hex(), expected)


def password_needs_rehash(password_hash: str) -> bool:
    """True if the stored hash uses fewer iterations than current policy.

    Callers re-hash on the next successful login so users are quietly upgraded.
    """
    parts = password_hash.split("$")
    if len(parts) != 4:
        return True  # legacy 3-part is below the current cost
    try:
        return int(parts[1]) < PBKDF2_ITERATIONS
    except (ValueError, TypeError):
        return True


def _sign(payload: str) -> str:
    secret = get_settings().app_secret_key.encode()
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def create_token(user_id: int, role: str, pwd_changed_at: datetime | None = None) -> str:
    """Issue a signed token. ``pwd_at`` pins this token to the user's current
    password version; any later password change invalidates it."""
    expires_at = datetime.now(UTC) + timedelta(minutes=get_settings().token_ttl_minutes)
    payload = {
        "sub": user_id,
        "role": role,
        "exp": int(expires_at.timestamp()),
        "iat": int(datetime.now(UTC).timestamp()),
        "pwd_at": int(pwd_changed_at.timestamp()) if pwd_changed_at else 0,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    return f"{encoded}.{_sign(encoded)}"


def decode_token(token: str) -> dict:
    try:
        encoded, signature = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("Invalid token") from exc
    if not hmac.compare_digest(_sign(encoded), signature):
        raise ValueError("Invalid token signature")
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded.encode()).decode())
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed token payload") from exc
    if payload.get("exp", 0) < int(datetime.now(UTC).timestamp()):
        raise ValueError("Token has expired")
    return payload
