"""
RSI divergence detection.

Bullish divergence: price makes a lower low, RSI makes a higher low.
Bearish divergence: price makes a higher high, RSI makes a lower high.

Returns a score: 1.0 (strong bullish), 0.5 (weak bullish),
                 -1.0 (strong bearish), -0.5 (weak bearish), 0.0 (none)

Pivot alignment: pivots are identified by bar index in the shared DataFrame.
Price and RSI pivots at the SAME positional index refer to the same bar —
this is correct because both series are aligned to df.index. The key fix vs
the old approach is that we now pair price-pivot index with its RSI value at
the SAME index (not at a separately found RSI pivot index), which guarantees
temporal alignment. We then verify that RSI also forms a pivot near that bar
to confirm the divergence is a genuine dual-pivot pattern.
"""
import numpy as np
import pandas as pd
import ta as ta_lib


def _find_pivot_highs(series: pd.Series, window: int = 5) -> list[int]:
    """Return positional indices of local highs in series."""
    highs = []
    for i in range(window, len(series) - window):
        segment = series.iloc[i - window : i + window + 1]
        if series.iloc[i] == segment.max():
            highs.append(i)
    return highs


def _find_pivot_lows(series: pd.Series, window: int = 5) -> list[int]:
    """Return positional indices of local lows in series."""
    lows = []
    for i in range(window, len(series) - window):
        segment = series.iloc[i - window : i + window + 1]
        if series.iloc[i] == segment.min():
            lows.append(i)
    return lows


def detect_rsi_divergence(
    df: pd.DataFrame,
    rsi_period: int = 14,
    pivot_window: int = 5,
    lookback_pivots: int = 3,
    min_pivot_separation: int = 10,
) -> float:
    """
    Returns divergence score from -1.0 to 1.0.

    Pivot alignment strategy: find price pivots, then read the RSI value at
    the SAME bar index. This guarantees price and RSI are compared on the
    same bar rather than on independently found RSI pivots that may differ
    by several bars. We additionally require that the RSI value at the price
    pivot is itself a local RSI extreme (within ±window bars) to filter noise.

    min_pivot_separation: minimum bar distance required between the two reference
    pivots. Adjacent pivots produce trivial noise-divergence; ≥10 bars ensures
    both swing points represent meaningful, distinct price moves.
    """
    if len(df) < rsi_period + pivot_window * 2 + 5:
        return 0.0

    rsi = ta_lib.momentum.RSIIndicator(close=df["close"], window=rsi_period).rsi()
    if rsi is None or rsi.isna().all():
        return 0.0

    price = df["close"].values
    rsi_vals = rsi.values

    # Find price pivot highs and lows
    price_series = pd.Series(price)
    rsi_series   = pd.Series(rsi_vals)

    high_pivots = _find_pivot_highs(price_series, pivot_window)
    low_pivots  = _find_pivot_lows(price_series, pivot_window)

    def _is_rsi_local_extreme(idx: int, kind: str) -> bool:
        """Check if rsi_vals[idx] is a local high/low within pivot_window bars."""
        lo = max(0, idx - pivot_window)
        hi = min(len(rsi_vals) - 1, idx + pivot_window)
        segment = rsi_vals[lo : hi + 1]
        if kind == "high":
            return float(rsi_vals[idx]) >= float(segment.max()) - 1e-9
        return float(rsi_vals[idx]) <= float(segment.min()) + 1e-9

    # ── Bearish divergence: price higher high, RSI lower high ───────────────
    # Use the last two price highs; read RSI at those same bar indices.
    valid_highs = [i for i in high_pivots if not np.isnan(rsi_vals[i])]
    if len(valid_highs) >= 2:
        p1, p2 = valid_highs[-2], valid_highs[-1]   # older, newer price high
        if (
            (p2 - p1) >= min_pivot_separation         # pivots are genuinely separated
            and price[p2] > price[p1]                 # price higher high
            and rsi_vals[p2] < rsi_vals[p1]           # RSI lower high (diverge)
            and rsi_vals[p2] > 60                     # RSI still elevated
            and _is_rsi_local_extreme(p1, "high")     # RSI was also a local high
            and _is_rsi_local_extreme(p2, "high")
        ):
            # Strength: relative RSI deterioration, symmetric denominator = mean
            mean_rsi = (abs(rsi_vals[p1]) + abs(rsi_vals[p2])) / 2.0
            denom = max(mean_rsi, 1e-6)
            strength = abs(rsi_vals[p1] - rsi_vals[p2]) / denom
            return -1.0 if strength > 0.05 else -0.5

    # ── Bullish divergence: price lower low, RSI higher low ─────────────────
    valid_lows = [i for i in low_pivots if not np.isnan(rsi_vals[i])]
    if len(valid_lows) >= 2:
        p1, p2 = valid_lows[-2], valid_lows[-1]      # older, newer price low
        if (
            (p2 - p1) >= min_pivot_separation         # pivots are genuinely separated
            and price[p2] < price[p1]                 # price lower low
            and rsi_vals[p2] > rsi_vals[p1]           # RSI higher low (diverge)
            and rsi_vals[p2] < 40                     # RSI still depressed
            and _is_rsi_local_extreme(p1, "low")      # RSI was also a local low
            and _is_rsi_local_extreme(p2, "low")
        ):
            mean_rsi = (abs(rsi_vals[p1]) + abs(rsi_vals[p2])) / 2.0
            denom = max(mean_rsi, 1e-6)
            strength = abs(rsi_vals[p2] - rsi_vals[p1]) / denom
            return 1.0 if strength > 0.05 else 0.5

    return 0.0
