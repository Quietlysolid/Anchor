from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.database.models import EquityCurvePoint


class EquityRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert(self, point: EquityCurvePoint) -> None:
        self.session.add(point)
        await self.session.flush()

    async def get_latest(self) -> Optional[EquityCurvePoint]:
        result = await self.session.execute(
            select(EquityCurvePoint).order_by(desc(EquityCurvePoint.time)).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_range(self, start: datetime, end: datetime) -> List[EquityCurvePoint]:
        from sqlalchemy import and_
        result = await self.session.execute(
            select(EquityCurvePoint)
            .where(
                and_(
                    EquityCurvePoint.time >= start,
                    EquityCurvePoint.time <= end,
                )
            )
            .order_by(EquityCurvePoint.time)
        )
        return list(result.scalars().all())

    async def get_all(self, limit: int = 2000) -> List[EquityCurvePoint]:
        result = await self.session.execute(
            select(EquityCurvePoint)
            .order_by(EquityCurvePoint.time)
            .limit(limit)
        )
        return list(result.scalars().all())
