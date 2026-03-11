from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.database.models import Position, Trade, PositionStatus
from anchor.utils.time_utils import utcnow


class PositionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert(self, position: Position) -> Position:
        self.session.add(position)
        await self.session.flush()
        await self.session.refresh(position)
        return position

    async def get_by_id(self, position_id: UUID) -> Optional[Position]:
        result = await self.session.execute(
            select(Position).where(Position.id == position_id)
        )
        return result.scalar_one_or_none()

    async def get_by_oanda_trade_id(self, oanda_trade_id: str) -> Optional[Position]:
        result = await self.session.execute(
            select(Position).where(Position.oanda_trade_id == oanda_trade_id)
        )
        return result.scalar_one_or_none()

    async def get_open(self) -> List[Position]:
        result = await self.session.execute(
            select(Position).where(Position.status == PositionStatus.OPEN)
        )
        return list(result.scalars().all())

    async def get_open_by_instrument(self, instrument: str) -> List[Position]:
        result = await self.session.execute(
            select(Position).where(
                and_(
                    Position.instrument == instrument,
                    Position.status == PositionStatus.OPEN,
                )
            )
        )
        return list(result.scalars().all())

    async def update_unrealized_pl(self, position_id: UUID, unrealized_pl: float) -> None:
        position = await self.get_by_id(position_id)
        if position:
            position.unrealized_pl = unrealized_pl
            await self.session.flush()

    async def close_position(
        self,
        position_id: UUID,
        exit_price: float,
        unrealized_pl: float,
    ) -> None:
        position = await self.get_by_id(position_id)
        if position:
            position.status = PositionStatus.CLOSED
            position.unrealized_pl = unrealized_pl
            await self.session.flush()

    # ── Methods called by Reconciler ──────────────────────────────────────────

    async def get_open_positions(self) -> List[Position]:
        """Alias used by the reconciler."""
        return await self.get_open()

    async def mark_closed(
        self,
        position_id: UUID,
        close_reason: str,
        closed_at: datetime,
        realized_pl: float = 0.0,
        exit_price: float | None = None,
    ) -> None:
        """Mark a DB position as closed and write a Trade record."""
        position = await self.get_by_id(position_id)
        if position is None:
            return

        position.status    = PositionStatus.CLOSED
        position.closed_at = closed_at
        position.realized_pl = Decimal(str(realized_pl))
        if exit_price is not None:
            position.current_price = Decimal(str(exit_price))

        # Write a Trade record for P&L tracking
        net_pl = realized_pl
        trade = Trade(
            position_id=position.id,
            instrument=position.instrument,
            direction=position.direction,
            units=position.units,
            entry_price=position.avg_entry_price,
            exit_price=Decimal(str(exit_price)) if exit_price else position.avg_entry_price,
            opened_at=position.opened_at,
            closed_at=closed_at,
            gross_pl=Decimal(str(net_pl)),
            net_pl=Decimal(str(net_pl)),
            close_reason=close_reason,
            signal_id=position.signal_id,
        )
        self.session.add(trade)
        await self.session.flush()

    async def mark_partial_tp_done(self, position_id: UUID, units_closed: int) -> None:
        """Record that the first partial TP close has been executed."""
        position = await self.get_by_id(position_id)
        if position is None:
            return
        position.partial_tp_done = True
        # Reduce tracked units by the amount closed
        remaining = max(Decimal(0), position.units - Decimal(str(units_closed)))
        if position.initial_units is None:
            position.initial_units = position.units
        position.units = remaining
        await self.session.flush()

    async def update_stop_loss(self, position_id: UUID, new_sl: float) -> None:
        """Update stop loss after moving to breakeven."""
        position = await self.get_by_id(position_id)
        if position:
            position.stop_loss = Decimal(str(new_sl))
            await self.session.flush()

    async def create_from_broker_trade(self, broker_trade: dict) -> Position:
        """Reconstruct a Position from an OANDA trade dict (for crash recovery)."""
        position = Position(
            instrument=broker_trade.get("instrument", "UNKNOWN"),
            direction="LONG" if float(broker_trade.get("currentUnits", 0)) > 0 else "SHORT",
            units=Decimal(str(abs(float(broker_trade.get("currentUnits", 0))))),
            avg_entry_price=Decimal(str(broker_trade.get("price", 0))),
            current_price=Decimal(str(broker_trade.get("price", 0))),
            unrealized_pl=Decimal(str(broker_trade.get("unrealizedPL", 0))),
            oanda_trade_id=broker_trade.get("id"),
            status=PositionStatus.OPEN,
            opened_at=utcnow(),
        )
        self.session.add(position)
        await self.session.flush()
        return position


class TradeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert(self, trade: Trade) -> Trade:
        self.session.add(trade)
        await self.session.flush()
        await self.session.refresh(trade)
        return trade

    async def get_all(self, limit: int = 500) -> List[Trade]:
        from sqlalchemy import desc
        result = await self.session.execute(
            select(Trade).order_by(desc(Trade.opened_at)).limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_instrument(self, instrument: str) -> List[Trade]:
        result = await self.session.execute(
            select(Trade).where(Trade.instrument == instrument)
        )
        return list(result.scalars().all())
