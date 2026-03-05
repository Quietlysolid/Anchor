from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from anchor.database.engine import get_session
from anchor.database.models import EconomicEvent
from anchor.utils.time_utils import utcnow

router = APIRouter()


@router.get("/calendar/upcoming")
async def get_upcoming_events(
    hours_ahead: int = 48,
    impact:      str = "HIGH",
    session:     AsyncSession = Depends(get_session),
):
    from datetime import timedelta
    now = utcnow()
    end = now + timedelta(hours=hours_ahead)
    impacts = [i.strip().upper() for i in impact.split(",")]

    q = (
        select(EconomicEvent)
        .where(
            and_(
                EconomicEvent.event_time >= now,
                EconomicEvent.event_time <= end,
                EconomicEvent.impact.in_(impacts),
            )
        )
        .order_by(EconomicEvent.event_time)
    )
    result = await session.execute(q)
    events = result.scalars().all()
    return [
        {
            "event_time": e.event_time.isoformat(),
            "currency":   e.currency,
            "impact":     e.impact,
            "event_name": e.event_name,
            "forecast":   e.forecast,
            "previous":   e.previous,
        }
        for e in events
    ]
