import asyncio
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from anchor.database.engine import get_db
from anchor.database.models import Position
from anchor.execution.broker_client import BrokerClient
from anchor.utils.time_utils import utcnow

router = APIRouter()


@router.get("/positions")
async def get_open_positions(session: AsyncSession = Depends(get_db)):
    del session
    broker = BrokerClient()
    try:
        positions = await asyncio.wait_for(broker.get_open_positions(), timeout=12)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Timed out loading broker positions") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Broker positions unavailable: {exc}") from exc
    return [_live_position_to_dict(p) for p in positions]


@router.get("/positions/history")
async def get_position_history(
    limit: int = 100,
    session: AsyncSession = Depends(get_db),
):
    q = select(Position).where(Position.status == "CLOSED").order_by(desc(Position.closed_at)).limit(limit)
    result = await session.execute(q)
    positions = result.scalars().all()
    return [_position_to_dict(p) for p in positions]


@router.get("/positions/{position_id}")
async def get_position(position_id: str, session: AsyncSession = Depends(get_db)):
    broker = BrokerClient()
    try:
        positions = await asyncio.wait_for(broker.get_open_positions(), timeout=12)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Timed out loading broker positions") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Broker positions unavailable: {exc}") from exc
    live_match = next((p for p in positions if str(p.get("id")) == position_id or str(p.get("instrument")) == position_id), None)
    if live_match:
        return _live_position_to_dict(live_match)

    q = select(Position).where(Position.id == position_id)
    result = await session.execute(q)
    position = result.scalar_one_or_none()
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    return _position_to_dict(position)


def _position_to_dict(p: Position) -> dict:
    return {
        "id":              str(p.id),
        "opened_at":       p.opened_at.isoformat(),
        "closed_at":       p.closed_at.isoformat() if p.closed_at else None,
        "instrument":      p.instrument,
        "direction":       p.direction,
        "units":           float(p.units),
        "avg_entry_price": float(p.avg_entry_price),
        "current_price":   float(p.current_price) if p.current_price else None,
        "unrealized_pl":   float(p.unrealized_pl) if p.unrealized_pl else None,
        "realized_pl":     float(p.realized_pl),
        "stop_loss":       float(p.stop_loss) if p.stop_loss else None,
        "take_profit":     float(p.take_profit) if p.take_profit else None,
        "broker_trade_id": p.oanda_trade_id,
        "status":          p.status,
        "record_origin":   "current_broker_state" if p.status == "OPEN" else "anchor_audit_history",
    }


def _live_position_to_dict(p: dict) -> dict:
    current_units = float(p.get("currentUnits", 0.0) or 0.0)
    now_iso = utcnow().astimezone(timezone.utc).isoformat()
    return {
        "id":              str(p.get("id") or p.get("instrument")),
        "opened_at":       now_iso,
        "closed_at":       None,
        "instrument":      str(p.get("instrument", "")),
        "direction":       "LONG" if current_units > 0 else "SHORT",
        "units":           abs(current_units),
        "avg_entry_price": float(p.get("price", 0.0) or 0.0),
        "current_price":   None,
        "unrealized_pl":   float(p.get("unrealizedPL", 0.0) or 0.0),
        "realized_pl":     0.0,
        "stop_loss":       None,
        "take_profit":     None,
        "broker_trade_id": str(p.get("instrument", "")),
        "status":          "OPEN",
        "record_origin":   "current_broker_state",
    }
