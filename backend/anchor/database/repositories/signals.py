from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.database.models import Signal


class SignalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert(self, signal: Signal) -> Signal:
        self.session.add(signal)
        await self.session.flush()
        await self.session.refresh(signal)
        return signal

    async def get_by_id(self, signal_id: UUID) -> Optional[Signal]:
        result = await self.session.execute(
            select(Signal).where(Signal.id == signal_id)
        )
        return result.scalar_one_or_none()

    async def get_latest(self, limit: int = 50) -> List[Signal]:
        result = await self.session.execute(
            select(Signal).order_by(desc(Signal.created_at)).limit(limit)
        )
        return list(result.scalars().all())

    async def get_active(self) -> List[Signal]:
        """Non-suppressed signals in the last 4 hours."""
        from datetime import timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
        result = await self.session.execute(
            select(Signal)
            .where(
                and_(
                    Signal.suppressed == False,  # noqa: E712
                    Signal.created_at >= cutoff,
                )
            )
            .order_by(desc(Signal.created_at))
        )
        return list(result.scalars().all())

    async def get_by_instrument(
        self, instrument: str, start: datetime, end: datetime
    ) -> List[Signal]:
        result = await self.session.execute(
            select(Signal)
            .where(
                and_(
                    Signal.instrument == instrument,
                    Signal.created_at >= start,
                    Signal.created_at <= end,
                )
            )
            .order_by(Signal.created_at)
        )
        return list(result.scalars().all())
