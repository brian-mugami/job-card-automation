from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from app.models import (
    InvoiceType,
    JobCardStatus,
    PaymentStatus,
    ServiceOption,
    StockMovementType,
    UserRole,
)

# --------------------------------------------------------------------------- #
# Password policy
# --------------------------------------------------------------------------- #


MIN_PASSWORD_LENGTH = 10


def _validate_password_strength(value: str) -> str:
    """Enforce a sane minimum: at least 10 chars, mixing letters and digits.

    Applied to *new* passwords (registration, admin user-create, reset, change).
    NOT applied to ``LoginRequest`` so users with legacy weaker passwords can
    still sign in (and be prompted to reset).
    """
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if not any(c.isalpha() for c in value):
        raise ValueError("Password must include at least one letter")
    if not any(c.isdigit() for c in value):
        raise ValueError("Password must include at least one digit")
    return value


StrongPassword = Annotated[str, AfterValidator(_validate_password_strength)]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    email: EmailStr
    # Length only — strength rules apply to *new* passwords, not legacy logins.
    password: str = Field(min_length=6)
    full_name: str | None = None


class LoginResponse(ApiModel):
    access_token: str
    role: UserRole
    full_name: str
    is_bootstrap_admin: bool = False


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20)
    password: StrongPassword


class ChangePasswordRequest(BaseModel):
    """Self-service password change for an authenticated user."""

    current_password: str = Field(min_length=1)
    new_password: StrongPassword


class MessageResponse(BaseModel):
    message: str


class EmailStatusRead(BaseModel):
    enabled: bool
    configured: bool
    host: str
    port: int
    from_email: str
    username_set: bool
    password_set: bool


class TestEmailRequest(BaseModel):
    to_email: EmailStr


class UserCreate(BaseModel):
    full_name: str
    email: EmailStr
    password: StrongPassword
    role: UserRole = UserRole.user
    is_active: bool = True


class UserUpdate(BaseModel):
    full_name: str | None = None
    password: StrongPassword | None = None
    role: UserRole | None = None
    is_active: bool | None = None


class UserRead(ApiModel):
    id: int
    full_name: str
    email: EmailStr
    role: UserRole
    is_active: bool


class SupplierBase(BaseModel):
    name: str
    contact_person: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    notes: str | None = None
    is_active: bool = True


class SupplierCreate(SupplierBase):
    pass


class SupplierUpdate(BaseModel):
    name: str | None = None
    contact_person: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    notes: str | None = None
    is_active: bool | None = None


class SupplierRead(SupplierBase, ApiModel):
    id: int


class WorkerBase(BaseModel):
    full_name: str
    phone: str | None = None
    email: EmailStr | None = None
    skill: str | None = None
    is_active: bool = True


class WorkerCreate(WorkerBase):
    pass


class WorkerUpdate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    skill: str | None = None
    is_active: bool | None = None


class WorkerRead(WorkerBase, ApiModel):
    id: int


class CustomerBase(BaseModel):
    full_name: str
    phone: str
    email: EmailStr | None = None
    car_registration: str
    car_make: str
    car_type: str | None = None
    car_year: int | None = Field(default=None, ge=1950, le=2100)
    car_description: str | None = None
    county: str
    location_description: str | None = None
    service_option: ServiceOption = ServiceOption.in_shop
    is_active: bool = True


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    car_registration: str | None = None
    car_make: str | None = None
    car_type: str | None = None
    car_year: int | None = Field(default=None, ge=1950, le=2100)
    car_description: str | None = None
    county: str | None = None
    location_description: str | None = None
    service_option: ServiceOption | None = None
    is_active: bool | None = None


class CustomerRead(CustomerBase, ApiModel):
    id: int


class CustomerVehicleBase(BaseModel):
    customer_id: int
    registration: str
    make: str
    vehicle_type: str | None = None
    year: int | None = Field(default=None, ge=1950, le=2100)
    description: str | None = None
    is_active: bool = True


class CustomerVehicleCreate(CustomerVehicleBase):
    pass


class CustomerVehicleUpdate(BaseModel):
    customer_id: int | None = None
    registration: str | None = None
    make: str | None = None
    vehicle_type: str | None = None
    year: int | None = Field(default=None, ge=1950, le=2100)
    description: str | None = None
    is_active: bool | None = None


class CustomerVehicleRead(CustomerVehicleBase, ApiModel):
    id: int


class VehicleTransferRequest(BaseModel):
    new_customer_id: int
    notes: str | None = None


class VehicleOwnershipHistoryRead(ApiModel):
    id: int
    vehicle_id: int
    previous_customer_id: int
    new_customer_id: int
    notes: str | None = None


class WorkTypeBase(BaseModel):
    name: str
    description: str | None = None
    is_active: bool = True


class WorkTypeCreate(WorkTypeBase):
    pass


class WorkTypeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class WorkTypeRead(WorkTypeBase, ApiModel):
    id: int


class GarageCostBase(BaseModel):
    name: str
    default_amount: Decimal | None = None
    description: str | None = None
    is_active: bool = True


class GarageCostCreate(GarageCostBase):
    pass


class GarageCostUpdate(BaseModel):
    name: str | None = None
    default_amount: Decimal | None = None
    description: str | None = None
    is_active: bool | None = None


class GarageCostRead(GarageCostBase, ApiModel):
    id: int


class GarageItemBase(BaseModel):
    name: str
    category: str | None = None
    unit: str | None = None
    default_supplier_id: int | None = None
    description: str | None = None
    is_active: bool = True


class GarageItemCreate(GarageItemBase):
    pass


class GarageItemUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    unit: str | None = None
    default_supplier_id: int | None = None
    description: str | None = None
    is_active: bool | None = None


class GarageItemRead(GarageItemBase, ApiModel):
    id: int


class InventoryReceiptBase(BaseModel):
    garage_item_id: int
    supplier_id: int
    # Quantity is counted in whole units — the garage stocks 1, 2, 3 oil
    # filters, not 1.5. Money fields (unit_cost / sale_price) stay Decimal.
    quantity: int = Field(gt=0)
    unit_cost: Decimal = Field(ge=0)
    default_sale_price: Decimal | None = Field(default=None, ge=0)
    supplier_invoice_ref: str | None = None
    supplier_invoice_file_path: str | None = None
    notes: str | None = None
    is_active: bool = True


class InventoryReceiptCreate(InventoryReceiptBase):
    pass


class InventoryReceiptRead(ApiModel):
    """Read schema doesn't inherit Base — Base now constrains ``quantity`` to
    ``int``, which would refuse any legacy rows that stored a fractional
    value. Reads stay Decimal-tolerant; the UI rounds to whole units."""

    id: int
    garage_item_id: int
    supplier_id: int
    quantity: Decimal
    remaining_quantity: Decimal
    unit_cost: Decimal
    default_sale_price: Decimal | None = None
    supplier_invoice_ref: str | None = None
    supplier_invoice_file_path: str | None = None
    notes: str | None = None
    is_active: bool = True
    created_at: datetime
    item_name: str | None = None
    supplier_name: str | None = None


class InventoryReceiptUpdate(BaseModel):
    """Admin-only edit. ``reason`` is mandatory and stored in the audit trail."""

    supplier_id: int | None = None
    quantity: int | None = Field(default=None, gt=0)
    unit_cost: Decimal | None = Field(default=None, ge=0)
    default_sale_price: Decimal | None = Field(default=None, ge=0)
    supplier_invoice_ref: str | None = None
    notes: str | None = None
    reason: str = Field(min_length=3, max_length=500)


class InventoryReceiptDeleteRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class InventoryReceiptAuditRead(ApiModel):
    id: int
    inventory_receipt_id: int
    actor_user_id: int
    actor_name: str | None = None
    action: str
    snapshot_before: dict
    snapshot_after: dict | None = None
    reason: str
    created_at: datetime


class StockMovementRead(ApiModel):
    id: int
    garage_item_id: int
    supplier_id: int | None = None
    inventory_receipt_id: int | None = None
    job_card_id: int | None = None
    movement_type: StockMovementType
    # Read-side stays Decimal so legacy rows that may have been written with
    # fractional quantities still serialise; the UI formats as whole units.
    quantity: Decimal
    unit_cost: Decimal | None = None
    notes: str | None = None


class StockBalanceRead(BaseModel):
    garage_item_id: int
    item_name: str
    category: str | None = None
    unit: str | None = None
    is_active: bool = True
    quantity_on_hand: Decimal
    latest_unit_cost: Decimal | None = None
    latest_sale_price: Decimal | None = None
    latest_supplier: str | None = None


class JobCardWorkInput(BaseModel):
    work_type_id: int
    worker_id: int | None = None
    notes: str | None = None
    labour_amount: Decimal = Field(default=0, ge=0)


class JobCardInventoryItemInput(BaseModel):
    garage_item_id: int
    # Whole units only — see InventoryReceiptBase for the rationale.
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    notes: str | None = None


class JobCardCostInput(BaseModel):
    garage_cost_id: int | None = None
    label: str
    amount: Decimal = Field(ge=0)
    cost_type: str | None = None


class JobCardCreate(BaseModel):
    customer_id: int | None = None
    new_customer: CustomerCreate | None = None
    vehicle_id: int | None = None
    new_vehicle: CustomerVehicleCreate | None = None
    intake_mode: ServiceOption = ServiceOption.in_shop
    picked_up_by_user_id: int | None = None
    pickup_cost_id: int | None = None
    pickup_cost_amount: Decimal | None = Field(default=None, ge=0)
    delivery_mode: ServiceOption = ServiceOption.in_shop
    dropoff_cost_id: int | None = None
    dropoff_cost_amount: Decimal | None = Field(default=None, ge=0)
    fuel_level: str | None = None
    mileage: str | None = None
    present_items: list[str] = Field(default_factory=list)
    other_present_items: str | None = None
    condition_notes: str | None = None
    works: list[JobCardWorkInput] = Field(default_factory=list)
    inventory_items: list[JobCardInventoryItemInput] = Field(default_factory=list)
    additional_costs: list[JobCardCostInput] = Field(default_factory=list)
    invoice_type: InvoiceType = InvoiceType.intermediate
    invoice_notes: str | None = None
    discount_amount: Decimal = Field(default=0, ge=0)
    tax_rate: Decimal = Field(default=0, ge=0)


class JobCardRead(ApiModel):
    id: int
    job_number: str
    customer_id: int
    vehicle_id: int
    status: JobCardStatus
    intake_mode: ServiceOption
    delivery_mode: ServiceOption
    fuel_level: str | None = None
    mileage: str | None = None
    subtotal: Decimal
    discount_amount: Decimal
    tax_rate: Decimal = Decimal("0.00")
    total_amount: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_email: str | None = None
    vehicle_registration: str | None = None
    vehicle_make: str | None = None
    vehicle_type: str | None = None
    invoice_number: str | None = None
    invoice_total: Decimal | None = None


class JobCardStatusUpdate(BaseModel):
    status: JobCardStatus


class JobCardHeaderUpdate(BaseModel):
    """Partial update for a job card's header fields. Lines are managed via
    their own endpoints so this stays small and safe."""

    intake_mode: ServiceOption | None = None
    picked_up_by_user_id: int | None = None
    pickup_cost_id: int | None = None
    pickup_cost_amount: Decimal | None = Field(default=None, ge=0)
    delivery_mode: ServiceOption | None = None
    dropoff_cost_id: int | None = None
    dropoff_cost_amount: Decimal | None = Field(default=None, ge=0)
    fuel_level: str | None = None
    mileage: str | None = None
    present_items: list[str] | None = None
    other_present_items: str | None = None
    condition_notes: str | None = None
    invoice_notes: str | None = None
    discount_amount: Decimal | None = Field(default=None, ge=0)
    tax_rate: Decimal | None = Field(default=None, ge=0)


class JobCardInvoiceCreate(BaseModel):
    invoice_type: InvoiceType = InvoiceType.intermediate
    notes: str | None = None


class InvoiceRead(ApiModel):
    id: int
    invoice_number: str
    job_card_id: int
    invoice_type: InvoiceType
    subtotal: Decimal
    vat_amount: Decimal
    tax_rate: Decimal
    discount_amount: Decimal
    total_amount: Decimal
    payment_status: PaymentStatus
    amount_paid: Decimal
    balance_due: Decimal
    notes: str | None = None
    is_sent_email: bool
    is_sent_whatsapp: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class InvoiceLineRead(ApiModel):
    id: int | None = None
    invoice_id: int | None = None
    line_order: int | None = None
    source: str
    source_id: int | None = None
    item: str
    description: str
    quantity: Decimal
    rate: Decimal
    amount: Decimal


class InvoiceShareRead(BaseModel):
    invoice_id: int
    whatsapp_url: str | None = None
    emailed: bool = False
    message: str


class InvoicePaymentUpdate(BaseModel):
    """Caller sets ``amount_paid``; the server derives ``payment_status`` to
    keep the two consistent. ``payment_status`` is kept on the schema for
    backwards compatibility but is ignored by the endpoint."""

    amount_paid: Decimal = Field(default=0, ge=0)
    payment_status: PaymentStatus | None = None  # accepted but ignored
