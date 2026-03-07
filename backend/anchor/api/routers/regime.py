from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from anchor.database.engine import get_db
from anchor.database.models import RegimeHistory

router = APIRouter()


@router.get("/regime/current")
async def get_current_regime(session: AsyncSession = Depends(get_db)):
    from sqlalchemy import func
    # Get latest regime per instrument
    subq = (
        select(
            RegimeHistory.instrument,
            func.max(RegimeHistory.time).label("max_time"),
        )
        .group_by(RegimeHistory.instrument)
        .subquery()
    )
    q = select(RegimeHistory).join(
        subq,
        (RegimeHistory.instrument == subq.c.instrument)
        & (RegimeHistory.time == subq.c.max_time),
    )
    result = await session.execute(q)
    regimes = result.scalars().all()
    return [
        {
            "instrument":  r.instrument,
            "regime":      r.regime,
            "confidence":  float(r.confidence) if r.confidence else None,
            "time":        r.time.isoformat(),
        }
        for r in regimes
    ]


@router.get("/regime/history")
async def get_regime_history(
    instrument: str | None = None,
    limit: int = 200,
    session: AsyncSession = Depends(get_db),
):
    q = select(RegimeHistory).order_by(desc(RegimeHistory.time)).limit(limit)
    if instrument:
        q = q.where(RegimeHistory.instrument == instrument)
    result = await session.execute(q)
    regimes = result.scalars().all()
    return [
        {
            "time":            r.time.isoformat(),
            "instrument":      r.instrument,
            "regime":          r.regime,
            "confidence":      float(r.confidence) if r.confidence else None,
            "transition_from": r.transition_from,
        }
        for r in regimes
    ]
