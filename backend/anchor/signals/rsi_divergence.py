"""
RSI divergence detection.

Bullish divergence: price makes a lower low, RSI makes a higher low.
Bearish divergence: price makes a higher high, RSI makes a lower high.

Returns a score: 1.0 (strong bullish), 0.5 (weak bullish),
                 -1.0 (strong bearish), -0.5 (weak bearish), 0.0 (none)
"""
import numpy as np
import pandas as pd
import ta as ta_lib


def _find_pivots(series: pd.Series, window: int = 5) -> tuple[list[int], list[int]]:
    """Find local highs and lows using rolling window."""
    highs, lows = [], []
    for i in range(window, len(series) - window):
        segment = series.iloc[i - window : i + window + 1]
        if series.iloc[i] == segment.max():
            highs.append(i)
        if series.iloc[i] == segment.min():
            lows.append(i)
    return highs, lows


def detect_rsi_divergence(
    df: pd.DataFrame,
    rsi_period: int = 14,
    pivot_window: int = 5,
    lookback_pivots: int = 3,
) -> float:
    """
    Returns divergence score from -1.0 to 1.0.
    Uses last `lookback_pivots` pivot pairs to detect divergence.
    """
    if len(df) < rsi_period + pivot_window * 2 + 5:
        return 0.0

    rsi = ta_lib.momentum.RSIIndicator(close=df["close"], window=rsi_period).rsi()
    if rsi is None or rsi.isna().all():
        return 0.0

    price = df["close"].values
    rsi_vals = rsi.values

    high_pivots, low_pivots = _find_pivots(df["close"], pivot_window)
    rsi_high_pivots, rsi_low_pivots = _find_pivots(
        pd.Series(rsi_vals, index=df.index), pivot_window
    )

    # Bearish divergence: price higher high, RSI lower high
    if len(high_pivots) >= 2 and len(rsi_high_pivots) >= 2:
        p1, p2 = high_pivots[-2], high_pivots[-1]
        r1, r2 = rsi_high_pivots[-2], rsi_high_pivots[-1]
        if (
            price[p2] > price[p1]           # price higher high
            and rsi_vals[r2] < rsi_vals[r1]  # RSI lower high
            and rsi_vals[r2] > 60            # RSI in overbought territory
        ):
            denom = max(abs(rsi_vals[r1]), 1e-6)
            strength = abs(rsi_vals[r1] - rsi_vals[r2]) / denom
            return -1.0 if strength > 0.05 else -0.5

    # Bullish divergence: price lower low, RSI higher low
    if len(low_pivots) >= 2 and len(rsi_low_pivots) >= 2:
        p1, p2 = low_pivots[-2], low_pivots[-1]
        r1, r2 = rsi_low_pivots[-2], rsi_low_pivots[-1]
        if (
            price[p2] < price[p1]           # price lower low
            and rsi_vals[r2] > rsi_vals[r1]  # RSI higher low
            and rsi_vals[r2] < 40            # RSI in oversold territory
        ):
            denom = max(abs(rsi_vals[r1]), 1e-6)
            strength = abs(rsi_vals[r2] - rsi_vals[r1]) / denom
            return 1.0 if strength > 0.05 else 0.5

    return 0.0
