from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, and_, desc
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.database.models import MarketData, TickData


class MarketDataRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert_candle(self, candle: MarketData) -> None:
        self.session.add(candle)
        await self.session.flush()

    async def bulk_insert(self, instrument: str, timeframe: str, df) -> int:
        """Insert a pandas DataFrame of OHLCV candles (index=time). ON CONFLICT DO NOTHING.

        Chunked at 500 rows to stay under asyncpg's 32767 bind-parameter limit
        (9 columns × 500 rows = 4500 params, well within the limit).
        """
        if df is None or df.empty:
            return 0
        rows = []
        for ts, row in df.iterrows():
            t = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            rows.append({
                "time": t,
                "instrument": instrument,
                "timeframe": timeframe,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]) if "volume" in row and row["volume"] else None,
                "spread_avg": float(row["spread"]) if "spread" in row and row["spread"] else None,
                "source": str(row["source"]) if "source" in row else "ibkr",
            })
        chunk_size = 500
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i : i + chunk_size]
            stmt = pg_insert(MarketData).values(chunk).on_conflict_do_nothing(
                index_elements=["time", "instrument", "timeframe"]
            )
            await self.session.execute(stmt)
        await self.session.flush()
        return len(rows)

    async def bulk_insert_candles(self, candles: List[MarketData]) -> int:
        if not candles:
            return 0
        rows = [
            {
                "time": c.time,
                "instrument": c.instrument,
                "timeframe": c.timeframe,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "spread_avg": c.spread_avg if hasattr(c, "spread_avg") else None,
                "source": c.source if hasattr(c, "source") and c.source else "ibkr",
            }
            for c in candles
        ]
        stmt = pg_insert(MarketData).values(rows).on_conflict_do_nothing(
            index_elements=["time", "instrument", "timeframe"]
        )
        await self.session.execute(stmt)
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
