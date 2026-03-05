"""
Multi-timeframe confirmation.

A 1H signal is only valid if higher timeframes agree.
Checks 4H and Daily trend alignment using EMAs and SMAs.

Score:
  1.0 = 4H + Daily both agree
  0.5 = only 4H agrees
  0.0 = Daily disagrees (signal blocked)
"""
import pandas as pd
import ta as ta_lib


def check_mtf_alignment(
    df_4h:   pd.DataFrame,
    df_daily: pd.DataFrame,
    direction: str,
) -> tuple[float, str]:
    """
    Returns (score: float, reason: str).
    direction: 'LONG' or 'SHORT'
    """
    score_4h    = _check_timeframe(df_4h,    direction, ema_fast=20, ema_slow=50)
    score_daily = _check_timeframe(df_daily, direction, sma=200)

    if score_daily == 0.0:
        return 0.0, "DAILY_DISAGREES"

    if score_4h == 0.0:
        return 0.5, "4H_DISAGREES"

    return 1.0, "ALL_AGREE"


def _check_timeframe(
    df: pd.DataFrame,
    direction: str,
    ema_fast: int | None = None,
    ema_slow: int | None = None,
    sma: int | None = None,
) -> float:
    """Returns 1.0 if timeframe agrees with direction, 0.0 if it disagrees."""
    if df is None or len(df) < 210:
        return 0.5  # insufficient data, neutral

    close = df["close"]
    current = float(close.iloc[-1])

    if ema_fast and ema_slow:
        fast = ta_lib.trend.EMAIndicator(close=close, window=ema_fast).ema_indicator()
        slow = ta_lib.trend.EMAIndicator(close=close, window=ema_slow).ema_indicator()
        if fast is None or slow is None:
            return 0.5

        fast_val = float(fast.iloc[-1])
        slow_val = float(slow.iloc[-1])

        if direction == "LONG":
            if current > fast_val and current > slow_val and fast_val > slow_val:
                return 1.0
            if current < slow_val:
                return 0.0
            return 0.5
        else:  # SHORT
            if current < fast_val and current < slow_val and fast_val < slow_val:
                return 1.0
            if current > slow_val:
                return 0.0
            return 0.5

    if sma:
        sma_series = ta_lib.trend.SMAIndicator(close=close, window=sma).sma_indicator()
        if sma_series is None:
            return 0.5

        sma_val = float(sma_series.iloc[-1])

        if direction == "LONG":
            return 1.0 if current > sma_val else 0.0
        else:
            return 1.0 if current < sma_val else 0.0

    return 0.5
