"""Human-readable serial numbering for job cards and invoices."""
from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import Base


async def next_number(session: AsyncSession, model: type[Base], prefix: str) -> str:
    """Generate a unique-ish ``<prefix><yyyymmdd>-<serial>-<random>`` number.

    The 4-char random hex suffix sidesteps the count-then-insert race that
    would otherwise let two concurrent calls both produce the same serial.
    The destination column's unique index is the last line of defence; if
    that ever fires, callers should retry. Collision probability per pair of
    concurrent inserts is 1 / 65,536 — small enough to ignore in practice.
    """
    count = await session.scalar(select(func.count()).select_from(model))
    serial = int(count or 0) + 1
    suffix = secrets.token_hex(2)
    return f"{prefix}{datetime.now().strftime('%Y%m%d')}-{serial:04d}-{suffix}"
