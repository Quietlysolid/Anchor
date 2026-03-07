"""
Bollinger Bands + Keltner Channels squeeze (TTM Squeeze).

Squeeze ON:  BB is inside KC → low volatility, coiling for a move.
Squeeze OFF: BB expands outside KC → breakout in progress.

Score with timing decay:
  - Squeeze fired this bar (bar 0 after release):  1.0  (best entry)
  - Fired 1-3 bars ago:                            0.85 (still valid)
  - Fired 4-10 bars ago:                           0.65 (trailing edge)
  - Fired >10 bars ago:                            0.0  (move is over)
  - Still in squeeze (coiling):                    0.4
  - No squeeze:                                    0.0

Rationale: entering a squeeze breakout many bars after the release means
chasing a move already in progress. The score decays linearly so older
fires contribute less to confluence.
"""
import pandas as pd
import ta as ta_lib


def detect_squeeze(
    df: pd.DataFrame,
    bb_period: int = 20,
    bb_std: float = 2.0,
    kc_period: int = 20,
    kc_multiplier: float = 1.5,
    lookback: int = 12,
) -> tuple[float, bool]:
    """
    Returns (score: float, squeeze_on: bool).
    squeeze_on = True means currently in compression.
    lookback: number of bars to look back for a recent squeeze fire.
    """
    if len(df) < bb_period + lookback + 2:
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

    def _squeeze_on(i: int) -> bool:
        return (
            bb_upper.iloc[i] < kc_upper.iloc[i]
            and bb_lower.iloc[i] > kc_lower.iloc[i]
        )

    # Current bar: still in squeeze → coiling score
    if _squeeze_on(-1):
        return 0.4, True

    # Scan back up to `lookback` bars for the most recent squeeze-fire transition
    # (was ON at bar i-1, OFF at bar i)
    for bars_ago in range(0, lookback + 1):
        idx_now  = -(1 + bars_ago)
        idx_prev = -(2 + bars_ago)
        if abs(idx_prev) > len(bb_upper):
            break
        if _squeeze_on(idx_prev) and not _squeeze_on(idx_now):
            # Squeeze fired `bars_ago` bars ago
            if bars_ago == 0:
                return 1.0, False    # fired this bar
            elif bars_ago <= 3:
                return 0.85, False   # 1-3 bars ago: fresh
            elif bars_ago <= 10:
                return 0.65, False   # 4-10 bars ago: trailing
            else:
                return 0.0, False    # stale

    return 0.0, False
