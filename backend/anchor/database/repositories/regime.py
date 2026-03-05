from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.database.models import RegimeHistory


class RegimeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert(self, entry: RegimeHistory) -> None:
        self.session.add(entry)
        await self.session.flush()

    async def get_current(self, instrument: str) -> Optional[RegimeHistory]:
        result = await self.session.execute(
            select(RegimeHistory)
            .where(RegimeHistory.instrument == instrument)
            .order_by(desc(RegimeHistory.detected_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_history(self, instrument: str, limit: int = 100) -> List[RegimeHistory]:
        result = await self.session.execute(
            select(RegimeHistory)
            .where(RegimeHistory.instrument == instrument)
            .order_by(desc(RegimeHistory.detected_at))
            .limit(limit)
        )
        rows = list(result.scalars().all())
        rows.reverse()
        return rows

    async def get_all_current(self) -> List[RegimeHistory]:
        """Get the most recent regime for each instrument."""
        from sqlalchemy import func
        subq = (
            select(
                RegimeHistory.instrument,
                func.max(RegimeHistory.detected_at).label("max_at"),
            )
            .group_by(RegimeHistory.instrument)
            .subquery()
        )
        result = await self.session.execute(
            select(RegimeHistory).join(
                subq,
                and_(
                    RegimeHistory.instrument == subq.c.instrument,
                    RegimeHistory.detected_at == subq.c.max_at,
                ),
            )
        )
        return list(result.scalars().all())
