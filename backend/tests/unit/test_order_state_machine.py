"""
Unit tests for OrderManager state machine.

All DB and broker I/O is mocked. Tests verify:
  - Valid transitions succeed and update in-memory cache
  - Invalid transitions raise InvalidTransitionError
  - Terminal states cannot be left
  - Redis publish is called on each transition
  - cancel() guards against double-cancellation
  - Unknown orders load from DB
"""
import json
import uuid

import pytest
from unittest.mock import AsyncMock, MagicMock

from anchor.execution.order_manager import OrderManager
from anchor.execution.order_types import (
    InvalidTransitionError,
    OrderState,
    TERMINAL_STATES,
    TRANSITIONS,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def order_repo():
    repo = MagicMock()
    repo.create       = AsyncMock()
    repo.get          = AsyncMock(return_value=None)
    repo.update_state = AsyncMock()
    repo.set_oanda_id = AsyncMock()
    repo.get_pending  = AsyncMock(return_value=[])
    repo.insert_fill  = AsyncMock()
    repo.session      = MagicMock()
    repo.session.add  = MagicMock()
    repo.session.flush = AsyncMock()
    return repo


@pytest.fixture
def broker():
    b = MagicMock()
    b.place_order  = AsyncMock(return_value=("oanda-123", 1.0800, "trade-456"))
    b.cancel_order = AsyncMock()
    return b


@pytest.fixture
def redis():
    r = MagicMock()
    r.publish = AsyncMock()
    return r


@pytest.fixture
def manager(order_repo, broker, redis):
    return OrderManager(order_repo, broker, redis=redis)


@pytest.fixture
def manager_no_redis(order_repo, broker):
    return OrderManager(order_repo, broker, redis=None)


# ── Valid transitions ─────────────────────────────────────────────────────────

class TestValidTransitions:
    @pytest.mark.asyncio
    async def test_pending_to_submitted(self, manager):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.PENDING
        await manager.transition(oid, OrderState.SUBMITTED, {})
        assert manager._order_states[oid] == OrderState.SUBMITTED

    @pytest.mark.asyncio
    async def test_submitted_to_acknowledged(self, manager):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.SUBMITTED
        await manager.transition(oid, OrderState.ACKNOWLEDGED, {})
        assert manager._order_states[oid] == OrderState.ACKNOWLEDGED

    @pytest.mark.asyncio
    async def test_acknowledged_to_partial(self, manager):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.ACKNOWLEDGED
        await manager.transition(oid, OrderState.PARTIAL, {"filled_so_far": 500})
        assert manager._order_states[oid] == OrderState.PARTIAL

    @pytest.mark.asyncio
    async def test_partial_to_filled(self, manager):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.PARTIAL
        await manager.transition(oid, OrderState.FILLED, {"fill_price": 1.0800})
        assert manager._order_states[oid] == OrderState.FILLED

    @pytest.mark.asyncio
    async def test_submitted_to_rejected(self, manager):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.SUBMITTED
        await manager.transition(oid, OrderState.REJECTED, {"reason": "margin"})
        assert manager._order_states[oid] == OrderState.REJECTED

    @pytest.mark.asyncio
    async def test_submitted_to_cancelled(self, manager):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.SUBMITTED
        await manager.transition(oid, OrderState.CANCELLED, {})
        assert manager._order_states[oid] == OrderState.CANCELLED

    @pytest.mark.asyncio
    async def test_pending_to_cancelled(self, manager):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.PENDING
        await manager.transition(oid, OrderState.CANCELLED, {})
        assert manager._order_states[oid] == OrderState.CANCELLED

    @pytest.mark.asyncio
    async def test_db_update_called_on_transition(self, manager, order_repo):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.PENDING
        await manager.transition(oid, OrderState.SUBMITTED, {"detail": "x"})
        order_repo.update_state.assert_awaited_once_with(oid, OrderState.SUBMITTED, {"detail": "x"})


# ── Invalid / terminal transitions ────────────────────────────────────────────

class TestInvalidTransitions:
    @pytest.mark.asyncio
    async def test_filled_is_terminal(self, manager):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.FILLED
        with pytest.raises(InvalidTransitionError):
            await manager.transition(oid, OrderState.CANCELLED, {})

    @pytest.mark.asyncio
    async def test_cancelled_is_terminal(self, manager):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.CANCELLED
        with pytest.raises(InvalidTransitionError):
            await manager.transition(oid, OrderState.SUBMITTED, {})

    @pytest.mark.asyncio
    async def test_rejected_is_terminal(self, manager):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.REJECTED
        with pytest.raises(InvalidTransitionError):
            await manager.transition(oid, OrderState.ACKNOWLEDGED, {})

    @pytest.mark.asyncio
    async def test_expired_is_terminal(self, manager):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.EXPIRED
        with pytest.raises(InvalidTransitionError):
            await manager.transition(oid, OrderState.FILLED, {})

    @pytest.mark.asyncio
    async def test_pending_cannot_go_directly_to_filled(self, manager):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.PENDING
        with pytest.raises(InvalidTransitionError):
            await manager.transition(oid, OrderState.FILLED, {})

    @pytest.mark.asyncio
    async def test_partial_cannot_go_to_rejected(self, manager):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.PARTIAL
        with pytest.raises(InvalidTransitionError):
            await manager.transition(oid, OrderState.REJECTED, {})

    @pytest.mark.asyncio
    async def test_in_memory_state_unchanged_on_invalid(self, manager):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.FILLED
        with pytest.raises(InvalidTransitionError):
            await manager.transition(oid, OrderState.CANCELLED, {})
        assert manager._order_states[oid] == OrderState.FILLED


# ── Redis publish ─────────────────────────────────────────────────────────────

class TestRedisPublish:
    @pytest.mark.asyncio
    async def test_redis_published_on_transition(self, manager, redis):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.PENDING
        await manager.transition(oid, OrderState.SUBMITTED, {})
        redis.publish.assert_awaited_once()
        channel, payload = redis.publish.call_args[0]
        assert channel == "orders"
        data = json.loads(payload)
        assert data["data"]["to_state"] == "SUBMITTED"
        assert data["data"]["from_state"] == "PENDING"

    @pytest.mark.asyncio
    async def test_no_redis_does_not_raise(self, manager_no_redis):
        oid = uuid.uuid4()
        manager_no_redis._order_states[oid] = OrderState.PENDING
        await manager_no_redis.transition(oid, OrderState.SUBMITTED, {})
        assert manager_no_redis._order_states[oid] == OrderState.SUBMITTED


# ── Unknown order DB fallback ─────────────────────────────────────────────────

class TestUnknownOrderFallback:
    @pytest.mark.asyncio
    async def test_unknown_order_raises_value_error_if_not_in_db(self, manager, order_repo):
        order_repo.get.return_value = None
        oid = uuid.uuid4()
        with pytest.raises(ValueError, match="not found"):
            await manager.transition(oid, OrderState.SUBMITTED, {})

    @pytest.mark.asyncio
    async def test_unknown_order_loads_from_db_and_transitions(self, manager, order_repo):
        oid = uuid.uuid4()
        db_order = MagicMock()
        db_order.state = OrderState.PENDING.value
        order_repo.get.return_value = db_order
        await manager.transition(oid, OrderState.SUBMITTED, {})
        assert manager._order_states[oid] == OrderState.SUBMITTED


class TestSubmitFlow:
    @pytest.mark.asyncio
    async def test_market_submit_immediate_fill_follows_valid_path(self, manager, order_repo):
        request = MagicMock()
        request.signal_id = uuid.uuid4()
        request.instrument = "EUR_USD"
        request.direction.value = "LONG"
        request.order_type.value = "MARKET"
        request.units = 1000
        request.stop_loss = 1.075
        request.take_profit = 1.085
        request.limit_price = None
        request.trailing_stop_distance = None

        order_id = await manager.submit(request)

        assert manager._order_states[order_id] == OrderState.FILLED
        transitioned_states = [call.args[1] for call in order_repo.update_state.await_args_list]
        assert transitioned_states == [
            OrderState.SUBMITTED,
            OrderState.ACKNOWLEDGED,
            OrderState.FILLED,
        ]


# ── Cancel guard ──────────────────────────────────────────────────────────────

class TestCancelGuard:
    @pytest.mark.asyncio
    async def test_cancel_skips_terminal_state(self, manager, order_repo):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.FILLED
        await manager.cancel(oid, reason="test")
        order_repo.update_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_transitions_non_terminal(self, manager):
        oid = uuid.uuid4()
        manager._order_states[oid] = OrderState.SUBMITTED
        await manager.cancel(oid, reason="user request")
        assert manager._order_states[oid] == OrderState.CANCELLED


# ── State machine definition integrity ────────────────────────────────────────

class TestStateMachineDefinition:
    def test_all_terminal_states_have_no_outgoing_transitions(self):
        for state in TERMINAL_STATES:
            assert TRANSITIONS[state] == set(), f"{state} should have no allowed transitions"

    def test_every_order_state_has_entry_in_transitions(self):
        for state in OrderState:
            assert state in TRANSITIONS, f"{state} missing from TRANSITIONS"

    def test_transitions_only_reference_valid_states(self):
        valid = set(OrderState)
        for src, targets in TRANSITIONS.items():
            for tgt in targets:
                assert tgt in valid, f"Unknown target state {tgt} in TRANSITIONS[{src}]"
