from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.database.models import Order, OrderEvent, Fill, OrderState
from anchor.utils.time_utils import utcnow


class OrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        order_id: UUID,
        signal_id: UUID | None,
        instrument: str,
        direction: str,
        order_type: str,
        requested_units: int,
        state: str,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        limit_price: float | None = None,
        trailing_stop_distance: float | None = None,
    ) -> Order:
        order = Order(
            id=order_id,
            signal_id=signal_id,
            instrument=instrument,
            direction=direction,
            order_type=order_type,
            requested_units=Decimal(str(requested_units)),
            state=state,
            stop_loss=Decimal(str(stop_loss)) if stop_loss is not None else None,
            take_profit=Decimal(str(take_profit)) if take_profit is not None else None,
            limit_price=Decimal(str(limit_price)) if limit_price is not None else None,
            trailing_stop_distance=Decimal(str(trailing_stop_distance)) if trailing_stop_distance is not None else None,
        )
        self.session.add(order)
        await self.session.flush()
        return order

    async def get(self, order_id: UUID) -> Optional[Order]:
        result = await self.session.execute(
            select(Order).where(Order.id == order_id)
        )
        return result.scalar_one_or_none()

    async def set_oanda_id(self, order_id: UUID, oanda_order_id: str) -> None:
        order = await self.get(order_id)
        if order:
            order.oanda_order_id = oanda_order_id
            await self.session.flush()

    async def update_state(self, order_id: UUID, new_state, event_data: dict) -> None:
        order = await self.get(order_id)
        if order is None:
            return
        old_state = order.state
        order.state = new_state.value if hasattr(new_state, "value") else str(new_state)

        now = utcnow()
        state_val = new_state.value if hasattr(new_state, "value") else str(new_state)
        if state_val == "SUBMITTED":
            order.submitted_at = now
        elif state_val == "ACKNOWLEDGED":
            order.acknowledged_at = now
        elif state_val == "FILLED":
            order.filled_at = now
        elif state_val == "CANCELLED":
            order.cancelled_at = now

        event = OrderEvent(
            order_id=order_id,
            from_state=old_state,
            to_state=state_val,
            event_data=event_data,
        )
        self.session.add(event)
        await self.session.flush()

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

    async def get_stale_pending(self, cutoff: datetime) -> List[Order]:
        """Return pending/submitted orders created before cutoff (for cancellation)."""
        from sqlalchemy import or_
        result = await self.session.execute(
            select(Order).where(
                and_(
                    or_(
                        Order.state == OrderState.PENDING,
                        Order.state == OrderState.SUBMITTED,
                    ),
                    Order.created_at < cutoff,
                )
            )
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
