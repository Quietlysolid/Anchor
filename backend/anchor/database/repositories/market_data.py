from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.database.models import MarketData, TickData


class MarketDataRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert_candle(self, candle: MarketData) -> None:
        self.session.add(candle)
        await self.session.flush()

    async def bulk_insert_candles(self, candles: List[MarketData]) -> int:
        self.session.add_all(candles)
        await self.session.flush()
        return len(candles)

    async def get_candles(
        self,
        instrument: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 5000,
    ) -> List[MarketData]:
        result = await self.session.execute(
            select(MarketData)
            .where(
                and_(
                    MarketData.instrument == instrument,
                    MarketData.timeframe == timeframe,
                    MarketData.time >= start,
                    MarketData.time <= end,
                )
            )
            .order_by(MarketData.time)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_latest_candle(self, instrument: str, timeframe: str) -> Optional[MarketData]:
        result = await self.session.execute(
            select(MarketData)
            .where(
                and_(
                    MarketData.instrument == instrument,
                    MarketData.timeframe == timeframe,
                )
            )
            .order_by(desc(MarketData.time))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_n_candles(
        self, instrument: str, timeframe: str, n: int
    ) -> List[MarketData]:
        result = await self.session.execute(
            select(MarketData)
            .where(
                and_(
                    MarketData.instrument == instrument,
                    MarketData.timeframe == timeframe,
                )
            )
            .order_by(desc(MarketData.time))
            .limit(n)
        )
        rows = list(result.scalars().all())
        rows.reverse()
        return rows

    async def insert_tick(self, tick: TickData) -> None:
        self.session.add(tick)
        await self.session.flush()

    async def get_latest_tick(self, instrument: str) -> Optional[TickData]:
        result = await self.session.execute(
            select(TickData)
            .where(TickData.instrument == instrument)
            .order_by(desc(TickData.time))
            .limit(1)
        )
        return result.scalar_one_or_none()
