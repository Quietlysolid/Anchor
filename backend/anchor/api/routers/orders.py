from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from anchor.database.engine import get_session
from anchor.database.models import Order

router = APIRouter()


@router.get("/orders")
async def get_orders(
    status: str | None = None,
    limit:  int = 100,
    session: AsyncSession = Depends(get_session),
):
    q = select(Order).order_by(desc(Order.created_at)).limit(limit)
    if status:
        statuses = [s.strip().upper() for s in status.split(",")]
        q = q.where(Order.state.in_(statuses))
    result = await session.execute(q)
    orders = result.scalars().all()
    return [_order_to_dict(o) for o in orders]


@router.get("/orders/{order_id}")
async def get_order(order_id: str, session: AsyncSession = Depends(get_session)):
    from fastapi import HTTPException
    from anchor.database.models import OrderEvent
    q = select(Order).where(Order.id == order_id)
    result = await session.execute(q)
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    q_events = select(OrderEvent).where(OrderEvent.order_id == order_id).order_by(OrderEvent.event_at)
    events_result = await session.execute(q_events)
    events = events_result.scalars().all()
    d = _order_to_dict(order)
    d["events"] = [
        {"event_at": e.event_at.isoformat(), "from": e.from_state, "to": e.to_state}
        for e in events
    ]
    return d


def _order_to_dict(o: Order) -> dict:
    return {
        "id":             str(o.id),
        "created_at":     o.created_at.isoformat(),
        "instrument":     o.instrument,
        "direction":      o.direction,
        "order_type":     o.order_type,
        "units":          float(o.requested_units),
        "state":          o.state,
        "oanda_order_id": o.oanda_order_id,
        "stop_loss":      float(o.stop_loss)    if o.stop_loss    else None,
        "take_profit":    float(o.take_profit)  if o.take_profit  else None,
    }
