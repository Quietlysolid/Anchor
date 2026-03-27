import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from anchor.database.engine import get_db
from anchor.database.models import Order
from anchor.execution.broker_client import BrokerClient
from anchor.utils.time_utils import utcnow

router = APIRouter()


@router.get("/orders")
async def get_orders(
    status: str | None = None,
    limit:  int = 100,
    session: AsyncSession = Depends(get_db),
):
    requested_statuses = [s.strip().upper() for s in status.split(",")] if status else None
    if requested_statuses is None or set(requested_statuses).issubset({"PENDING", "SUBMITTED", "ACKNOWLEDGED", "PARTIAL"}):
        broker = BrokerClient()
        try:
            orders = await asyncio.wait_for(broker.get_pending_orders(), timeout=12)
        except TimeoutError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=504, detail="Timed out loading broker orders") from exc
        except Exception as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail=f"Broker orders unavailable: {exc}") from exc
        filtered = [
            _live_order_to_dict(order)
            for order in orders
            if requested_statuses is None or str(order.get("state", "")).upper() in requested_statuses
        ]
        return filtered[:limit]

    q = select(Order).order_by(desc(Order.created_at)).limit(limit)
    if requested_statuses:
        q = q.where(Order.state.in_(requested_statuses))
    result = await session.execute(q)
    orders = result.scalars().all()
    return [_order_to_dict(o) for o in orders]


@router.get("/orders/{order_id}")
async def get_order(order_id: str, session: AsyncSession = Depends(get_db)):
    from fastapi import HTTPException
    from anchor.database.models import OrderEvent
    broker = BrokerClient()
    try:
        live_orders = await asyncio.wait_for(broker.get_pending_orders(), timeout=12)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Timed out loading broker orders") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Broker orders unavailable: {exc}") from exc
    live_match = next((o for o in live_orders if str(o.get("id")) == order_id), None)
    if live_match:
        return _live_order_to_dict(live_match)

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
        "broker_order_id": o.oanda_order_id,
        "stop_loss":      float(o.stop_loss)    if o.stop_loss    else None,
        "take_profit":    float(o.take_profit)  if o.take_profit  else None,
        "record_origin":  "current_broker_state" if o.state in {"PENDING", "SUBMITTED", "ACKNOWLEDGED", "PARTIAL"} else "anchor_audit_history",
    }


def _live_order_to_dict(o: dict) -> dict:
    return {
        "id":              str(o.get("id")),
        "created_at":      utcnow().isoformat(),
        "instrument":      str(o.get("instrument", "")),
        "direction":       str(o.get("direction", "LONG")),
        "order_type":      str(o.get("order_type", "MARKET")),
        "units":           float(o.get("units", 0.0) or 0.0),
        "state":           str(o.get("state", "")),
        "broker_order_id": str(o.get("id")),
        "stop_loss":       None,
        "take_profit":     None,
        "record_origin":   "current_broker_state",
    }
