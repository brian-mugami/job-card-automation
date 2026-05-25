"""Inventory consumption + refund helpers.

These are the small handful of operations that need to mutate stock atomically
alongside whatever business event triggered them (a job card moving to
in-progress, a cancellation refunding parts). Keeping them on a service module
rather than on the API router makes them easy to call from anywhere and easy
to unit-test in isolation.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


async def stock_balance(session: AsyncSession, garage_item_id: int) -> Decimal:
    """Current on-hand quantity for an item.

    Sums ``InventoryReceipt.remaining_quantity`` over active receipts. We also
    write ``StockMovement`` rows for the audit trail, but the running total
    lives on the receipts so FIFO consumption is a single scan.
    """
    balance = await session.scalar(
        select(func.coalesce(func.sum(models.InventoryReceipt.remaining_quantity), 0)).where(
            models.InventoryReceipt.garage_item_id == garage_item_id,
            models.InventoryReceipt.is_active.is_(True),
        )
    )
    return _money(balance)


async def consume_stock(
    session: AsyncSession,
    job_card_id: int,
    item: schemas.JobCardInventoryItemInput,
) -> models.JobCardItem:
    """Deduct ``item.quantity`` from oldest receipts first (FIFO).

    Each receipt that contributes to the consumption gets a ``stock_out``
    movement row so the audit trail is complete, and the cost we attribute to
    the job-card line is the weighted average across the receipts touched.
    """
    garage_item = await session.get(models.GarageItem, item.garage_item_id)
    if not garage_item or not garage_item.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Garage item is inactive and cannot be used on a job card.",
        )

    available = await stock_balance(session, item.garage_item_id)
    quantity = _money(item.quantity)
    if available < quantity:
        name = garage_item.name if garage_item else "Selected item"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not enough stock for {name}. Available: {available}",
        )

    receipts = await session.scalars(
        select(models.InventoryReceipt)
        .where(
            models.InventoryReceipt.garage_item_id == item.garage_item_id,
            models.InventoryReceipt.remaining_quantity > 0,
            models.InventoryReceipt.is_active.is_(True),
        )
        .order_by(models.InventoryReceipt.id.asc())
    )
    remaining = quantity
    total_cost = Decimal("0.00")
    for receipt in receipts:
        if remaining <= 0:
            break
        used = min(_money(receipt.remaining_quantity), remaining)
        receipt.remaining_quantity = _money(receipt.remaining_quantity) - used
        total_cost += used * _money(receipt.unit_cost)
        session.add(
            models.StockMovement(
                garage_item_id=item.garage_item_id,
                supplier_id=receipt.supplier_id,
                inventory_receipt_id=receipt.id,
                job_card_id=job_card_id,
                movement_type=models.StockMovementType.stock_out,
                quantity=used,
                unit_cost=receipt.unit_cost,
                notes=item.notes,
            )
        )
        remaining -= used

    average_cost = _money(total_cost / quantity) if quantity else Decimal("0.00")
    job_item = models.JobCardItem(
        job_card_id=job_card_id,
        garage_item_id=item.garage_item_id,
        quantity=quantity,
        unit_cost=average_cost,
        unit_price=_money(item.unit_price),
        amount=_money(quantity * _money(item.unit_price)),
        notes=item.notes,
        is_active=True,
    )
    session.add(job_item)
    return job_item


async def refund_stock_for_job_item(
    session: AsyncSession,
    job_card_id: int,
    garage_item_id: int,
    quantity_to_refund: Decimal,
) -> Decimal:
    """Reverse stock movements for a single (job, item) up to ``quantity_to_refund``.

    Used when an admin removes a part line from an existing job card. Walks
    the existing ``stock_out`` movements in LIFO order (newest first), nets
    out any prior ``stock_in`` reversals attached to the same receipt, and
    writes fresh ``stock_in`` rows to cover whichever portion is still
    outstanding. The receipt's ``remaining_quantity`` is bumped in lockstep.

    Returns the actual quantity refunded — typically equal to
    ``quantity_to_refund`` but may be less if the original consumption was
    smaller (e.g. the line was edited after the fact).
    """
    quantity_to_refund = _money(quantity_to_refund)
    if quantity_to_refund <= 0:
        return Decimal("0.00")

    outs = (
        await session.scalars(
            select(models.StockMovement)
            .where(
                models.StockMovement.job_card_id == job_card_id,
                models.StockMovement.garage_item_id == garage_item_id,
                models.StockMovement.movement_type == models.StockMovementType.stock_out,
            )
            .order_by(models.StockMovement.id.desc())
        )
    ).all()

    remaining = quantity_to_refund
    refunded = Decimal("0.00")
    for out in outs:
        if remaining <= 0:
            break
        already_refunded = await session.scalar(
            select(func.coalesce(func.sum(models.StockMovement.quantity), 0)).where(
                models.StockMovement.job_card_id == job_card_id,
                models.StockMovement.garage_item_id == garage_item_id,
                models.StockMovement.inventory_receipt_id == out.inventory_receipt_id,
                models.StockMovement.movement_type == models.StockMovementType.stock_in,
            )
        )
        net_out = _money(out.quantity) - _money(already_refunded)
        if net_out <= 0:
            continue

        portion = min(net_out, remaining)
        if out.inventory_receipt_id is not None:
            receipt = await session.get(models.InventoryReceipt, out.inventory_receipt_id)
            if receipt is not None:
                receipt.remaining_quantity = (
                    _money(receipt.remaining_quantity) + portion
                )

        session.add(
            models.StockMovement(
                garage_item_id=out.garage_item_id,
                supplier_id=out.supplier_id,
                inventory_receipt_id=out.inventory_receipt_id,
                job_card_id=job_card_id,
                movement_type=models.StockMovementType.stock_in,
                quantity=portion,
                unit_cost=out.unit_cost,
                notes="Refund: job-card line removed",
            )
        )
        remaining -= portion
        refunded += portion
    return refunded


async def refund_stock_for_job(session: AsyncSession, job_card_id: int) -> int:
    """Reverse every stock-out movement attached to a job card.

    Used when a job card is cancelled after parts were already consumed.
    Restores ``remaining_quantity`` on the originating receipts and writes
    matching ``stock_in`` movements so the audit trail tells a coherent story.
    Idempotent: only reverses ``stock_out`` rows, so calling twice is a no-op.

    Returns the count of reversed movements.
    """
    movements = (
        await session.scalars(
            select(models.StockMovement).where(
                models.StockMovement.job_card_id == job_card_id,
                models.StockMovement.movement_type == models.StockMovementType.stock_out,
            )
        )
    ).all()

    reversed_count = 0
    for movement in movements:
        # Check if we've already reversed this one (a matching stock_in row
        # with the same receipt + quantity exists). Cheap idempotency guard.
        already_reversed = await session.scalar(
            select(func.count(models.StockMovement.id)).where(
                models.StockMovement.job_card_id == job_card_id,
                models.StockMovement.inventory_receipt_id == movement.inventory_receipt_id,
                models.StockMovement.movement_type == models.StockMovementType.stock_in,
                models.StockMovement.quantity == movement.quantity,
            )
        )
        if already_reversed:
            continue

        if movement.inventory_receipt_id is not None:
            receipt = await session.get(
                models.InventoryReceipt, movement.inventory_receipt_id
            )
            if receipt is not None:
                receipt.remaining_quantity = (
                    _money(receipt.remaining_quantity) + _money(movement.quantity)
                )

        session.add(
            models.StockMovement(
                garage_item_id=movement.garage_item_id,
                supplier_id=movement.supplier_id,
                inventory_receipt_id=movement.inventory_receipt_id,
                job_card_id=job_card_id,
                movement_type=models.StockMovementType.stock_in,
                quantity=movement.quantity,
                unit_cost=movement.unit_cost,
                notes="Refund: job card cancelled",
            )
        )
        reversed_count += 1
    return reversed_count
