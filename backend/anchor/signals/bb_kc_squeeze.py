"""
Bollinger Bands + Keltner Channels squeeze (TTM Squeeze).

Squeeze ON:  BB is inside KC → low volatility, coiling for a move.
Squeeze OFF: BB expands outside KC → breakout in progress.

Score:
  - Squeeze just fired (BB crossed outside KC) → 1.0
  - Still in squeeze (BB inside KC) → 0.3 (potential setup, not yet confirmed)
  - No squeeze → 0.0
"""
import pandas as pd
import ta as ta_lib


def detect_squeeze(
    df: pd.DataFrame,
    bb_period: int = 20,
    bb_std: float = 2.0,
    kc_period: int = 20,
    kc_multiplier: float = 1.5,
) -> tuple[float, bool]:
    """
    Returns (score: float, squeeze_on: bool).
    squeeze_on = True means currently in compression.
    """
    if len(df) < bb_period + 5:
        return 0.0, False

    # Bollinger Bands
    bb_indicator = ta_lib.volatility.BollingerBands(
        close=df["close"], window=bb_period, window_dev=bb_std
    )
    bb_upper = bb_indicator.bollinger_hband()
    bb_lower = bb_indicator.bollinger_lband()

    # Keltner Channels
    kc_indicator = ta_lib.volatility.KeltnerChannel(
        high=df["high"], low=df["low"], close=df["close"],
        window=kc_period, multiplier=kc_multiplier
    )
    kc_upper = kc_indicator.keltner_channel_hband()
    kc_lower = kc_indicator.keltner_channel_lband()

    if any(s is None or s.isna().all() for s in [bb_upper, bb_lower, kc_upper, kc_lower]):
        return 0.0, False

    # Squeeze ON: BB inside KC
    squeeze_now  = (bb_upper.iloc[-1] < kc_upper.iloc[-1]) and (bb_lower.iloc[-1] > kc_lower.iloc[-1])
    squeeze_prev = (bb_upper.iloc[-2] < kc_upper.iloc[-2]) and (bb_lower.iloc[-2] > kc_lower.iloc[-2])

    # Squeeze just fired (was ON, now OFF)
    if squeeze_prev and not squeeze_now:
        return 1.0, False

    if squeeze_now:
        return 0.3, True

    return 0.0, False
