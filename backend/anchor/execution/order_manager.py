"""
Order state machine.

Every state transition:
  1. Validates the transition is legal
  2. Writes to orders table (state update)
  3. Appends to order_events table (audit log)
  4. Publishes to Redis pub/sub for WebSocket fanout
  5. Raises InvalidTransitionError if illegal
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import structlog

from anchor.execution.order_types import (
    OrderState,
    OrderRequest,
    TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransitionError,
)
from anchor.utils.time_utils import utcnow

if TYPE_CHECKING:
    from anchor.execution.broker_client import BrokerClient

logger = structlog.get_logger(__name__)


class OrderManager:
    def __init__(self, order_repo, broker_client: "BrokerClient", redis=None):
        self.order_repo    = order_repo
        self.broker_client = broker_client
        self.redis         = redis

        # In-memory order state cache (reduces DB reads on hot path)
        # Must be loaded from DB on startup via load_open_orders()
        self._order_states: dict[uuid.UUID, OrderState] = {}
        self._partial_fills: dict[uuid.UUID, dict] = {}  # order_id → fill aggregation

    async def load_open_orders(self) -> None:
        """Hydrate in-memory state cache from DB on startup/restart.

        Call this once during engine startup to ensure the cache matches
        the persisted state. Without this, orders that were open during
        a crash/restart would have no in-memory state and fall back to
        a DB query on the first transition, which is safe but log-noisy.
        """
        try:
            open_orders = await self.order_repo.get_pending()
            for order in open_orders:
                self._order_states[order.id] = OrderState(order.state)
            logger.info("order_cache_loaded", count=len(open_orders))
        except Exception as exc:
            logger.error("order_cache_load_failed", error=str(exc))

    async def submit(self, request: OrderRequest) -> uuid.UUID:
        """Create and submit a new order. Returns order_id."""
        order_id = uuid.uuid4()

        # Create in DB as PENDING
        await self.order_repo.create(
            order_id=order_id,
            signal_id=request.signal_id,
            instrument=request.instrument,
            direction=request.direction.value,
            order_type=request.order_type.value,
            requested_units=request.units,
            state=OrderState.PENDING.value,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            limit_price=request.limit_price,
            trailing_stop_distance=request.trailing_stop_distance,
        )
        self._order_states[order_id] = OrderState.PENDING

        logger.info("order_created", order_id=str(order_id), instrument=request.instrument)

        # Transition → SUBMITTED and send to broker
        await self.transition(order_id, OrderState.SUBMITTED, {})

        oanda_id, fill_price, trade_id = await self.broker_client.place_order(order_id, request)
        await self.order_repo.set_oanda_id(order_id, oanda_id)

        # Market orders fill immediately — create Position row and mark FILLED
        if trade_id and fill_price:
            await self.transition(order_id, OrderState.ACKNOWLEDGED, {"oanda_order_id": oanda_id})
            await self._create_position(request, trade_id, fill_price)
            await self.transition(order_id, OrderState.FILLED, {"fill_price": fill_price})

        return order_id

    async def _create_position(
        self,
        request: OrderRequest,
        oanda_trade_id: str,
        fill_price: float,
    ) -> None:
        """Write a Position row after a market order fills."""
        from anchor.database.models import Position, PositionStatus
        from decimal import Decimal

        position = Position(
            instrument=request.instrument,
            direction=request.direction.value,
            units=Decimal(str(request.units)),
            avg_entry_price=Decimal(str(fill_price)),
            current_price=Decimal(str(fill_price)),
            stop_loss=Decimal(str(request.stop_loss)) if request.stop_loss else None,
            take_profit=Decimal(str(request.take_profit)) if request.take_profit else None,
            oanda_trade_id=oanda_trade_id,
            status=PositionStatus.OPEN,
            signal_id=request.signal_id,
        )
        self.order_repo.session.add(position)
        await self.order_repo.session.flush()
        logger.info(
            "position_opened",
            instrument=request.instrument,
            direction=request.direction.value,
            units=request.units,
            fill_price=fill_price,
            oanda_trade_id=oanda_trade_id,
        )

    async def transition(
        self,
        order_id: uuid.UUID,
        new_state: OrderState,
        event_data: dict,
    ) -> None:
        current = self._order_states.get(order_id)
        if current is None:
            # Load from DB
            order = await self.order_repo.get(order_id)
            if order is None:
                raise ValueError(f"Order {order_id} not found")
            current = OrderState(order.state)
            self._order_states[order_id] = current

        allowed = TRANSITIONS[current]
        if new_state not in allowed:
            raise InvalidTransitionError(
                f"Order {order_id}: cannot transition {current} → {new_state}"
            )

        # Write atomically
        await self.order_repo.update_state(order_id, new_state, event_data)

        self._order_states[order_id] = new_state

        logger.info(
            "order_transition",
            order_id=str(order_id),
            from_state=current.value,
            to_state=new_state.value,
        )

        # Publish to Redis for WebSocket
        if self.redis:
            await self.redis.publish(
                "orders",
                json.dumps({
                    "channel": "orders",
                    "data": {
                        "order_id":   str(order_id),
                        "from_state": current.value,
                        "to_state":   new_state.value,
                        "event_data": event_data,
                        "timestamp":  utcnow().isoformat(),
                    },
                }),
            )

    async def handle_fill(
        self,
        order_id: uuid.UUID,
        units_filled: int,
        fill_price: float,
        fill_at: datetime,
        oanda_fill_id: str | None = None,
        spread_at_fill: float | None = None,
    ) -> None:
        """Handle a fill event from OANDA (full or partial)."""
        order = await self.order_repo.get(order_id)
        if order is None:
            logger.warning("fill_for_unknown_order", order_id=str(order_id))
            return

        # Track partial fills for VWAP entry
        if order_id not in self._partial_fills:
            self._partial_fills[order_id] = {"total_units": 0, "vwap_numerator": 0.0}

        pf = self._partial_fills[order_id]
        pf["total_units"]     += units_filled
        pf["vwap_numerator"]  += units_filled * fill_price
        vwap_entry = pf["vwap_numerator"] / pf["total_units"]

        # Record fill
        from anchor.database.models import Fill
        fill = Fill(
            order_id=order_id,
            instrument=order.instrument,
            units_filled=units_filled,
            fill_price=fill_price,
            fill_at=fill_at,
            oanda_fill_id=oanda_fill_id,
            spread_at_fill=spread_at_fill,
        )
        await self.order_repo.insert_fill(fill)

        # Determine if fully filled
        total_filled = pf["total_units"]
        if total_filled >= int(order.requested_units):
            await self.transition(
                order_id,
                OrderState.FILLED,
                {"fill_price": fill_price, "vwap_entry": vwap_entry},
            )
            del self._partial_fills[order_id]
        else:
            await self.transition(
                order_id,
                OrderState.PARTIAL,
                {"filled_so_far": total_filled, "vwap_entry": vwap_entry},
            )

    async def cancel(self, order_id: uuid.UUID, reason: str = "") -> None:
        """Cancel an order (if not in terminal state)."""
        current = self._order_states.get(order_id, OrderState.PENDING)
        if current in TERMINAL_STATES:
            return
        await self.transition(order_id, OrderState.CANCELLED, {"reason": reason})
        # Clean up partial fill tracking to prevent memory leak
        self._partial_fills.pop(order_id, None)
        if self.broker_client:
            order = await self.order_repo.get(order_id)
            if order and order.oanda_order_id:
                await self.broker_client.cancel_order(order.oanda_order_id)
