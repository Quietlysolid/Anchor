from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.database.models import Order, OrderEvent, Fill, OrderState


class OrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert(self, order: Order) -> Order:
        self.session.add(order)
        await self.session.flush()
        await self.session.refresh(order)
        return order

    async def get_by_id(self, order_id: UUID) -> Optional[Order]:
        result = await self.session.execute(
            select(Order).where(Order.id == order_id)
        )
        return result.scalar_one_or_none()

    async def get_by_oanda_id(self, oanda_order_id: str) -> Optional[Order]:
        result = await self.session.execute(
            select(Order).where(Order.oanda_order_id == oanda_order_id)
        )
        return result.scalar_one_or_none()

    async def get_pending(self) -> List[Order]:
        result = await self.session.execute(
            select(Order).where(
                Order.state.in_([OrderState.PENDING, OrderState.SUBMITTED, OrderState.ACKNOWLEDGED])
            )
        )
        return list(result.scalars().all())

    async def get_recent(self, limit: int = 100) -> List[Order]:
        result = await self.session.execute(
            select(Order).order_by(desc(Order.created_at)).limit(limit)
        )
        return list(result.scalars().all())

    async def insert_event(self, event: OrderEvent) -> None:
        self.session.add(event)
        await self.session.flush()

    async def get_events(self, order_id: UUID) -> List[OrderEvent]:
        result = await self.session.execute(
            select(OrderEvent)
            .where(OrderEvent.order_id == order_id)
            .order_by(OrderEvent.event_at)
        )
        return list(result.scalars().all())

    async def insert_fill(self, fill: Fill) -> None:
        self.session.add(fill)
        await self.session.flush()

    async def get_fills(self, order_id: UUID) -> List[Fill]:
        result = await self.session.execute(
            select(Fill)
            .where(Fill.order_id == order_id)
            .order_by(Fill.fill_at)
        )
        return list(result.scalars().all())
