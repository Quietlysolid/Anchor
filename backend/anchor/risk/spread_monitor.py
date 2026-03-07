"""
Spread monitor. Suppresses entries if:
  1. Live spread exceeds session median by more than the configured multiplier
     (default 3x) — catches sudden spike events.
  2. Spread-to-ATR ratio exceeds the configured threshold (default 0.15) —
     ensures the cost of entry does not eat an excessive fraction of the
     expected move. A 3-pip spread against a 10-pip ATR means 30% of your
     TP is gone before the trade starts.
"""
import statistics
from collections import defaultdict, deque

import structlog

from anchor.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# Keep last 200 spread observations per instrument for rolling median
SPREAD_HISTORY_SIZE = 200

# Maximum acceptable spread as a fraction of ATR (e.g. 0.15 = 15%)
_MAX_SPREAD_ATR_RATIO = 0.15


class SpreadMonitor:
    def __init__(self):
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=SPREAD_HISTORY_SIZE))
        self._current: dict[str, float] = {}

    def update(self, instrument: str, bid: float, ask: float) -> None:
        spread = ask - bid
        self._current[instrument] = spread
        self._history[instrument].append(spread)

    async def check(self, instrument: str, atr: float | None = None) -> tuple[bool, str | None]:
        """Returns (allowed: bool, reason: str | None).

        atr: current ATR value (optional). When provided, also checks that
             spread / atr < _MAX_SPREAD_ATR_RATIO so the trade has enough
             room to breathe before the spread cost eats into the move.
        """
        current_spread = self._current.get(instrument)
        if current_spread is None:
            return True, None

        # Gate 1: absolute spike vs rolling median
        history = list(self._history[instrument])
        if len(history) >= 10:
            median_spread = statistics.median(history)
            if median_spread > 1e-10:
                ratio = current_spread / median_spread
                if ratio > settings.spread_spike_multiplier:
                    logger.debug(
                        "spread_spike",
                        instrument=instrument,
                        current=current_spread,
                        median=median_spread,
                        ratio=ratio,
                    )
                    return False, f"SPREAD_SPIKE:{ratio:.1f}x"

        # Gate 2: spread-to-ATR ratio (only when ATR is available)
        if atr is not None and atr > 1e-10:
            spread_atr_ratio = current_spread / atr
            if spread_atr_ratio > _MAX_SPREAD_ATR_RATIO:
                logger.debug(
                    "spread_atr_too_high",
                    instrument=instrument,
                    spread=current_spread,
                    atr=atr,
                    ratio=spread_atr_ratio,
                )
                return False, f"SPREAD_ATR_RATIO:{spread_atr_ratio:.2f}"

        return True, None

    def get_spread(self, instrument: str) -> float | None:
        return self._current.get(instrument)
