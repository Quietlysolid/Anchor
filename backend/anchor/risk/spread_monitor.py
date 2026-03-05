"""
Spread monitor. Suppresses entries if live spread exceeds
the session median by more than the configured multiplier (default 3x).
"""
from collections import defaultdict, deque

import structlog

from anchor.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# Keep last 200 spread observations per instrument for rolling median
SPREAD_HISTORY_SIZE = 200


class SpreadMonitor:
    def __init__(self):
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=SPREAD_HISTORY_SIZE))
        self._current: dict[str, float] = {}

    def update(self, instrument: str, bid: float, ask: float) -> None:
        spread = ask - bid
        self._current[instrument] = spread
        self._history[instrument].append(spread)

    async def check(self, instrument: str) -> tuple[bool, str | None]:
        """Returns (allowed: bool, reason: str | None)."""
        current_spread = self._current.get(instrument)
        if current_spread is None:
            return True, None

        history = list(self._history[instrument])
        if len(history) < 10:
            return True, None  # not enough data

        import statistics
        median_spread = statistics.median(history)
        if median_spread < 1e-10:
            return True, None

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

        return True, None

    def get_spread(self, instrument: str) -> float | None:
        return self._current.get(instrument)
