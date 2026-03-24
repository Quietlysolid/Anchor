from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from anchor.database.engine import get_db
from anchor.database.models import Position

router = APIRouter()


@router.get("/positions")
async def get_open_positions(session: AsyncSession = Depends(get_db)):
    q = select(Position).where(Position.status == "OPEN").order_by(desc(Position.opened_at))
    result = await session.execute(q)
    positions = result.scalars().all()
    return [_position_to_dict(p) for p in positions]


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
        "oanda_trade_id":  p.oanda_trade_id,
        "status":          p.status,
    }
