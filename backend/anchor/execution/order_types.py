"""Order type enums and dataclasses."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class OrderState(str, Enum):
    PENDING       = "PENDING"
    SUBMITTED     = "SUBMITTED"
    ACKNOWLEDGED  = "ACKNOWLEDGED"
    PARTIAL       = "PARTIAL"
    FILLED        = "FILLED"
    CANCELLED     = "CANCELLED"
    REJECTED      = "REJECTED"
    EXPIRED       = "EXPIRED"


class OrderType(str, Enum):
    MARKET     = "MARKET"
    LIMIT      = "LIMIT"
    STOP_LIMIT = "STOP_LIMIT"


class Direction(str, Enum):
    LONG  = "LONG"
    SHORT = "SHORT"


# Legal state transitions
TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.PENDING:       {OrderState.SUBMITTED, OrderState.CANCELLED},
    OrderState.SUBMITTED:     {OrderState.ACKNOWLEDGED, OrderState.REJECTED,
                               OrderState.CANCELLED, OrderState.EXPIRED},
    OrderState.ACKNOWLEDGED:  {OrderState.PARTIAL, OrderState.FILLED,
                               OrderState.CANCELLED, OrderState.EXPIRED},
    OrderState.PARTIAL:       {OrderState.FILLED, OrderState.CANCELLED,
                               OrderState.EXPIRED},
    OrderState.FILLED:        set(),
    OrderState.CANCELLED:     set(),
    OrderState.REJECTED:      set(),
    OrderState.EXPIRED:       set(),
}

TERMINAL_STATES = {
    OrderState.FILLED,
    OrderState.CANCELLED,
    OrderState.REJECTED,
    OrderState.EXPIRED,
}


class InvalidTransitionError(Exception):
    pass


@dataclass
class OrderRequest:
    instrument:             str
    direction:              Direction
    units:                  int
    order_type:             OrderType = OrderType.MARKET
    stop_loss:              float | None = None
    take_profit:            float | None = None
    limit_price:            float | None = None
    trailing_stop_distance: float | None = None
    signal_id:              uuid.UUID | None = None
    gtd_time:               datetime | None = None  # expiry for GTD limit orders


@dataclass
class FillRecord:
    order_id:       uuid.UUID
    instrument:     str
    units_filled:   int
    fill_price:     float
    fill_at:        datetime
    expected_price: float | None = None
    spread_at_fill: float | None = None
    oanda_fill_id:  str | None = None
