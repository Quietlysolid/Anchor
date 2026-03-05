from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.database.models import Position, Trade, PositionStatus


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
