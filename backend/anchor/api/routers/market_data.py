from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from anchor.database.engine import get_db
from anchor.database.models import MarketData

router = APIRouter()

# Map frontend timeframe strings to DB storage format
_TF_MAP = {"15m": "M15", "1h": "H1", "4h": "H4", "1d": "D"}


@router.get("/market-data/{instrument}/{timeframe}")
async def get_candles(
    instrument: str,
    timeframe:  str,
    limit:      int = 500,
    session:    AsyncSession = Depends(get_db),
):
    from sqlalchemy import desc
    tf = _TF_MAP.get(timeframe.lower(), timeframe.upper())
    q = (
        select(MarketData)
        .where(
            and_(
                MarketData.instrument == instrument.upper(),
                MarketData.timeframe  == tf,
            )
        )
        .order_by(desc(MarketData.time))
        .limit(limit)
    )
    result = await session.execute(q)
    candles = list(reversed(result.scalars().all()))
    return [
        {
            "time":   int(c.time.timestamp()),  # Unix seconds — matches Candle.time: number
            "open":   float(c.open),
            "high":   float(c.high),
            "low":    float(c.low),
            "close":  float(c.close),
            "volume": c.volume,
        }
        for c in candles
    ]
