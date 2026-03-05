"""Portfolio Manager — conflict resolution and net exposure control.

Sits above individual position sizer. Decides whether a new signal should
be acted on given the current portfolio state.

Rules:
1. Max 3 open positions at once (configurable)
2. Same-instrument position: reject if already open in same direction;
   allow adding if in opposite direction (reduce/close existing)
3. Correlation-blocked pairs: reject if net directional exposure > threshold
4. Max per-currency exposure: no more than 2 positions touching any single currency
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import structlog

from anchor.config import settings
from anchor.database.models import Position, Direction, PositionStatus
from anchor.risk.correlation import CorrelationMatrix

logger = structlog.get_logger(__name__)

MAX_OPEN_POSITIONS = 3
MAX_CURRENCY_EXPOSURE = 2  # max positions sharing a currency


class PortfolioManager:
    def __init__(self, correlation_matrix: CorrelationMatrix) -> None:
        self._corr = correlation_matrix

    def can_open(
        self,
        instrument: str,
        direction: str,
        open_positions: List[Position],
    ) -> Tuple[bool, str]:
        """Return (allowed, reason) for a potential new position."""

        # Gate 1: max open positions
        open_count = sum(1 for p in open_positions if p.status == PositionStatus.OPEN)
        if open_count >= MAX_OPEN_POSITIONS:
            return False, f"max_positions_reached ({open_count}/{MAX_OPEN_POSITIONS})"

        # Gate 2: no duplicate instrument + direction
        for p in open_positions:
            if p.status != PositionStatus.OPEN:
                continue
            if p.instrument == instrument and p.direction.value == direction:
                return False, f"duplicate_position ({instrument} {direction})"

        # Gate 3: currency exposure
        base, quote = instrument.split("_")
        currencies_in_use = set()
        for p in open_positions:
            if p.status != PositionStatus.OPEN:
                continue
            b, q = p.instrument.split("_")
            currencies_in_use.add(b)
            currencies_in_use.add(q)

        base_count = sum(
            1 for p in open_positions
            if p.status == PositionStatus.OPEN
            and (p.instrument.startswith(base) or p.instrument.endswith(base))
        )
        quote_count = sum(
            1 for p in open_positions
            if p.status == PositionStatus.OPEN
            and (p.instrument.startswith(quote) or p.instrument.endswith(quote))
        )
        if base_count >= MAX_CURRENCY_EXPOSURE:
            return False, f"currency_overexposed ({base}: {base_count})"
        if quote_count >= MAX_CURRENCY_EXPOSURE:
            return False, f"currency_overexposed ({quote}: {quote_count})"

        # Gate 4: correlation check
        open_instrument_directions = [
            (p.instrument, p.direction.value)
            for p in open_positions
            if p.status == PositionStatus.OPEN
        ]
        blocked, reason = self._corr.is_blocked(
            instrument, direction, open_instrument_directions
        )
        if blocked:
            return False, reason

        return True, "ok"
