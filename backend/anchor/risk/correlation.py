"""
Rolling correlation matrix across all traded pairs.
Updated hourly. Blocks new orders if correlation with existing
open positions exceeds threshold in the same effective direction.
"""
import numpy as np
import pandas as pd
import structlog

from anchor.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

LOOKBACK_DAYS = 45


class CorrelationManager:
    def __init__(self):
        self._matrix: pd.DataFrame | None = None

    async def update(self, daily_closes: dict[str, pd.Series]) -> None:
        """
        daily_closes: instrument → Series of daily close prices.
        Computes rolling 45-day log-return correlation.
        """
        if not daily_closes:
            return

        returns = {}
        for instrument, closes in daily_closes.items():
            if len(closes) >= 2:
                returns[instrument] = np.log(closes / closes.shift(1)).dropna()

        if len(returns) < 2:
            return

        df = pd.DataFrame(returns).dropna().tail(LOOKBACK_DAYS)
        self._matrix = df.corr()
        logger.debug("correlation_matrix_updated", pairs=list(returns.keys()))

    def check_new_position(
        self,
        instrument: str,
        direction: str,
        open_positions: list,
    ) -> tuple[bool, str | None]:
        """
        Returns (allowed: bool, blocking_reason: str | None).

        Blocks if any existing open position has effective correlation
        > threshold (same direction exposure).
        """
        if self._matrix is None or instrument not in self._matrix.index:
            return True, None  # no data, allow

        new_dir = 1 if direction == "LONG" else -1

        for pos in open_positions:
            other_instrument = pos.instrument
            if other_instrument == instrument:
                continue

            if other_instrument not in self._matrix.columns:
                continue

            corr = float(self._matrix.loc[instrument, other_instrument])
            pos_dir = 1 if pos.direction == "LONG" else -1

            # Effective exposure = correlation × direction alignment
            effective_exposure = corr * new_dir * pos_dir
            if effective_exposure > settings.correlation_block_threshold:
                return False, f"CORRELATED_WITH_{other_instrument}:{corr:.2f}"

        return True, None

    def compute_scale_factor(
        self,
        instrument: str,
        direction: str,
        open_positions: list,
        floor: float = 0.35,
    ) -> float:
        """
        Continuous position-size scale factor based on correlation overlap.

        For each open position, computes effective_overlap = corr × direction_alignment.
        scale = max(floor, 1.0 - max_effective_overlap)

        Examples (floor=0.35):
          EUR_USD LONG, NZD_USD LONG open (corr=0.85) → scale = max(0.35, 1-0.85) = 0.35
          EUR_USD LONG, NZD_USD SHORT open (corr=0.85) → overlap=-0.85, ignored → scale = 1.0
          EUR_USD LONG, NZD_USD LONG open (corr=0.55) → scale = max(0.35, 1-0.55) = 0.45
          no correlated open positions → scale = 1.0

        Returns 1.0 when matrix is unavailable (fail-open).
        """
        if self._matrix is None or instrument not in self._matrix.index:
            return 1.0

        new_dir = 1 if direction == "LONG" else -1
        max_overlap = 0.0

        for pos in open_positions:
            other = pos.instrument
            if other == instrument or other not in self._matrix.columns:
                continue

            corr = float(self._matrix.loc[instrument, other])
            # Handle both Direction enum and plain string
            _pos_dir_str = pos.direction.value if hasattr(pos.direction, "value") else str(pos.direction)
            pos_dir = 1 if _pos_dir_str == "LONG" else -1

            effective_overlap = corr * new_dir * pos_dir
            if effective_overlap > max_overlap:
                max_overlap = effective_overlap

        scale = max(floor, 1.0 - max_overlap)
        if scale < 1.0:
            logger.debug(
                "correlation_scale_computed",
                instrument=instrument,
                direction=direction,
                max_overlap=round(max_overlap, 3),
                scale=round(scale, 3),
            )
        return scale

    def get_correlation(self, instrument_a: str, instrument_b: str) -> float | None:
        if self._matrix is None:
            return None
        if instrument_a not in self._matrix.index:
            return None
        if instrument_b not in self._matrix.columns:
            return None
        return float(self._matrix.loc[instrument_a, instrument_b])
