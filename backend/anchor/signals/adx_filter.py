"""
ADX (Average Directional Index) regime filter.

ADX < 25: ranging market → mean reversion preferred
ADX 25-40: transitioning
ADX >= 40: trending → trend following preferred

Returns a score 0.0-1.0 whose direction depends on the signal type:
  - rsi_confirmed=True  (divergence/mean-reversion): high score when ADX is LOW
  - rsi_confirmed=False (trend-following):           high score when ADX is HIGH

Both score functions are mirrored piecewise-linear so the same threshold (0.65)
applies meaningfully to both paths.
"""
import pandas as pd
import ta as ta_lib


def compute_adx_score(
    df: pd.DataFrame,
    period: int = 14,
    ranging_threshold: float = 25.0,
    trending_threshold: float = 40.0,
    rsi_confirmed: bool = True,
) -> tuple[float, str]:
    """
    Returns (score: float, regime: str).

    score is 0.0-1.0.  Interpretation:
      rsi_confirmed=True  → mean-reversion suitability (high when ADX is low)
      rsi_confirmed=False → trend-following suitability (high when ADX is high)

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
        mean_rev_score = 1.0 - (adx_val / ranging_threshold) * 0.35
    elif adx_val < trending_threshold:
        regime = "TRANSITIONING"
        mean_rev_score = 0.65 - ((adx_val - ranging_threshold) / (trending_threshold - ranging_threshold)) * 0.35
    else:
        regime = "TRENDING"
        mean_rev_score = max(0.0, 0.3 - (adx_val - trending_threshold) / 50.0 * 0.3)

    if rsi_confirmed:
        # Divergence / mean-reversion signal: prefer low ADX (ranging)
        score = mean_rev_score
    else:
        # Trend-following signal: prefer high ADX (trending).
        # Mirror: trend_score = 1 - mean_rev_score, then clamp to [0, 1].
        score = round(max(0.0, min(1.0, 1.0 - mean_rev_score)), 4)

    return round(score, 4), regime
