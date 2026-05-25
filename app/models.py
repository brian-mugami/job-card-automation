from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class UserRole(StrEnum):
    admin = "admin"
    user = "user"


class ServiceOption(StrEnum):
    in_shop = "in_shop"
    pickup = "pickup"
    dropoff = "dropoff"
    pickup_and_dropoff = "pickup_and_dropoff"


class StockMovementType(StrEnum):
    stock_in = "stock_in"
    stock_out = "stock_out"


class JobCardStatus(StrEnum):
    draft = "draft"
    in_progress = "in_progress"
    ready = "ready"
    invoiced = "invoiced"
    complete = "complete"
    closed = "closed"
    cancelled = "cancelled"


class InvoiceType(StrEnum):
    intermediate = "intermediate"
    final = "final"


class PaymentStatus(StrEnum):
    not_paid = "not_paid"
    partially_paid = "partially_paid"
    paid = "paid"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.user)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Bumped on every password change (initial set, admin update, self-reset).
    # Tokens encode the value at issue time; later changes invalidate older
    # tokens so a reset effectively signs every active session out.
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PasswordResetToken(Base, TimestampMixin):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    contact_person: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Worker(Base, TimestampMixin):
    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120), index=True)
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(255))
    skill: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(160), index=True)
    phone: Mapped[str] = mapped_column(String(40), index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    # Legacy vehicle fields remain for local compatibility; new work should use CustomerVehicle.
    car_registration: Mapped[str] = mapped_column(String(40), index=True)
    car_model: Mapped[str] = mapped_column(String(120))
    car_type: Mapped[str | None] = mapped_column(String(120))
    car_year: Mapped[int | None] = mapped_column(Integer)
    car_description: Mapped[str | None] = mapped_column(Text)
    county: Mapped[str] = mapped_column(String(80))
    location_description: Mapped[str | None] = mapped_column(Text)
    service_option: Mapped[ServiceOption] = mapped_column(
        Enum(ServiceOption), default=ServiceOption.in_shop
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    vehicles: Mapped[list["CustomerVehicle"]] = relationship(
        back_populates="customer",
        cascade="all, delete-orphan",
    )

    @property
    def car_make(self) -> str:
        return self.car_model


class CustomerVehicle(Base, TimestampMixin):
    __tablename__ = "customer_vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    registration: Mapped[str] = mapped_column(String(40), index=True)
    make: Mapped[str] = mapped_column(String(120))
    vehicle_type: Mapped[str | None] = mapped_column(String(120))
    year: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    customer: Mapped[Customer] = relationship(back_populates="vehicles")


class VehicleOwnershipHistory(Base, TimestampMixin):
    __tablename__ = "vehicle_ownership_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("customer_vehicles.id"), index=True)
    previous_customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    new_customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    notes: Mapped[str | None] = mapped_column(Text)

    vehicle: Mapped[CustomerVehicle] = relationship()
    previous_customer: Mapped[Customer] = relationship(foreign_keys=[previous_customer_id])
    new_customer: Mapped[Customer] = relationship(foreign_keys=[new_customer_id])


class WorkType(Base, TimestampMixin):
    __tablename__ = "work_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class GarageCost(Base, TimestampMixin):
    __tablename__ = "garage_costs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    default_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class GarageItem(Base, TimestampMixin):
    __tablename__ = "garage_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    category: Mapped[str | None] = mapped_column(String(120))
    unit: Mapped[str | None] = mapped_column(String(40))
    default_supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    default_supplier: Mapped[Supplier | None] = relationship()


class InventoryReceipt(Base, TimestampMixin):
    __tablename__ = "inventory_receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    garage_item_id: Mapped[int] = mapped_column(ForeignKey("garage_items.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    remaining_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    default_sale_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    supplier_invoice_ref: Mapped[str | None] = mapped_column(String(120))
    supplier_invoice_file_path: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    garage_item: Mapped[GarageItem] = relationship()
    supplier: Mapped[Supplier] = relationship()


class InventoryReceiptAudit(Base, TimestampMixin):
    """Trail of admin edits / deletions on inventory receipts.

    Anything that mutates a receipt outside its normal lifecycle (consumption
    via job cards) writes one row here so an auditor can reconstruct what
    changed, who changed it, and the reason given. ``snapshot_before`` and
    ``snapshot_after`` are JSON blobs of the relevant receipt fields at the
    edges of the change.
    """

    __tablename__ = "inventory_receipt_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inventory_receipt_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_receipts.id", ondelete="CASCADE"), index=True
    )
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(20))  # 'edited' | 'deleted'
    snapshot_before: Mapped[dict] = mapped_column(JSON)
    snapshot_after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(String(500))

    inventory_receipt: Mapped["InventoryReceipt"] = relationship()
    actor: Mapped["User"] = relationship()


class StockMovement(Base, TimestampMixin):
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    garage_item_id: Mapped[int] = mapped_column(ForeignKey("garage_items.id"), index=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), index=True)
    inventory_receipt_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_receipts.id"))
    job_card_id: Mapped[int | None] = mapped_column(ForeignKey("job_cards.id"), index=True)
    movement_type: Mapped[StockMovementType] = mapped_column(Enum(StockMovementType))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    notes: Mapped[str | None] = mapped_column(Text)

    garage_item: Mapped[GarageItem] = relationship()
    supplier: Mapped[Supplier | None] = relationship()
    inventory_receipt: Mapped[InventoryReceipt | None] = relationship()


class JobCard(Base, TimestampMixin):
    __tablename__ = "job_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("customer_vehicles.id"), index=True)
    status: Mapped[JobCardStatus] = mapped_column(Enum(JobCardStatus), default=JobCardStatus.draft)
    intake_mode: Mapped[ServiceOption] = mapped_column(Enum(ServiceOption), default=ServiceOption.in_shop)
    picked_up_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    pickup_cost_id: Mapped[int | None] = mapped_column(ForeignKey("garage_costs.id"))
    pickup_cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    delivery_mode: Mapped[ServiceOption] = mapped_column(Enum(ServiceOption), default=ServiceOption.in_shop)
    dropoff_cost_id: Mapped[int | None] = mapped_column(ForeignKey("garage_costs.id"))
    dropoff_cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    fuel_level: Mapped[str | None] = mapped_column(String(60))
    mileage: Mapped[str | None] = mapped_column(String(60))
    present_items: Mapped[str | None] = mapped_column(Text)
    other_present_items: Mapped[str | None] = mapped_column(Text)
    condition_notes: Mapped[str | None] = mapped_column(Text)
    invoice_notes: Mapped[str | None] = mapped_column(Text)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    customer: Mapped[Customer] = relationship()
    vehicle: Mapped[CustomerVehicle] = relationship()
    picked_up_by: Mapped[User | None] = relationship(foreign_keys=[picked_up_by_user_id])
    pickup_cost: Mapped[GarageCost | None] = relationship(foreign_keys=[pickup_cost_id])
    dropoff_cost: Mapped[GarageCost | None] = relationship(foreign_keys=[dropoff_cost_id])


class JobCardWork(Base, TimestampMixin):
    __tablename__ = "job_card_works"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_card_id: Mapped[int] = mapped_column(ForeignKey("job_cards.id"), index=True)
    work_type_id: Mapped[int] = mapped_column(ForeignKey("work_types.id"))
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    labour_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    job_card: Mapped[JobCard] = relationship()
    work_type: Mapped[WorkType] = relationship()
    worker: Mapped[Worker | None] = relationship()


class JobCardItem(Base, TimestampMixin):
    __tablename__ = "job_card_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_card_id: Mapped[int] = mapped_column(ForeignKey("job_cards.id"), index=True)
    garage_item_id: Mapped[int] = mapped_column(ForeignKey("garage_items.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    job_card: Mapped[JobCard] = relationship()
    garage_item: Mapped[GarageItem] = relationship()


class JobCardCost(Base, TimestampMixin):
    __tablename__ = "job_card_costs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_card_id: Mapped[int] = mapped_column(ForeignKey("job_cards.id"), index=True)
    garage_cost_id: Mapped[int | None] = mapped_column(ForeignKey("garage_costs.id"))
    label: Mapped[str] = mapped_column(String(160))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    cost_type: Mapped[str | None] = mapped_column(String(60))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    job_card: Mapped[JobCard] = relationship()
    garage_cost: Mapped[GarageCost | None] = relationship()


class Invoice(Base, TimestampMixin):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    job_card_id: Mapped[int] = mapped_column(ForeignKey("job_cards.id"), index=True)
    invoice_type: Mapped[InvoiceType] = mapped_column(Enum(InvoiceType), default=InvoiceType.intermediate)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), default=PaymentStatus.not_paid
    )
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    is_sent_email: Mapped[bool] = mapped_column(Boolean, default=False)
    is_sent_whatsapp: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    job_card: Mapped[JobCard] = relationship()

    @property
    def balance_due(self) -> Decimal:
        return max(
            Decimal("0.00"),
            (self.total_amount or Decimal("0.00")) - (self.amount_paid or Decimal("0.00")),
        )
