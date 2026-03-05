"""
ADX (Average Directional Index) regime filter.

ADX < 20: ranging market → mean reversion preferred
ADX 20-30: transitioning
ADX > 30: trending → trend following preferred
ADX > 50: very strong trend

Returns a score 0.0-1.0 for mean reversion suitability (higher = better for mean rev)
and a regime string.
"""
import pandas as pd
import ta as ta_lib


def compute_adx_score(
    df: pd.DataFrame,
    period: int = 14,
    ranging_threshold: float = 25.0,
    trending_threshold: float = 40.0,
) -> tuple[float, str]:
    """
    Returns (score: float, regime: str).
    score is 0.0-1.0 suitability for MEAN REVERSION signals.
    regime is 'RANGING', 'TRANSITIONING', or 'TRENDING'.
    """
    if len(df) < period + 5:
        return 0.5, "UNKNOWN"

    adx_indicator = ta_lib.trend.ADXIndicator(
        high=df["high"], low=df["low"], close=df["close"], window=period
    )
    adx_series = adx_indicator.adx()
    if adx_series is None or adx_series.isna().all():
        return 0.5, "UNKNOWN"

    adx_val = float(adx_series.iloc[-1])
    if pd.isna(adx_val):
        return 0.5, "UNKNOWN"

    if adx_val < ranging_threshold:
        regime = "RANGING"
        score = 1.0 - (adx_val / ranging_threshold) * 0.35
    elif adx_val < trending_threshold:
        regime = "TRANSITIONING"
        score = 0.65 - ((adx_val - ranging_threshold) / (trending_threshold - ranging_threshold)) * 0.35
    else:
        regime = "TRENDING"
        score = max(0.0, 0.3 - (adx_val - trending_threshold) / 50.0 * 0.3)

    return round(score, 4), regime
