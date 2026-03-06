"""
Support and Resistance level detection with strength scoring.

Finds significant price levels by:
1. Identifying pivot highs/lows
2. Clustering nearby pivots into zones
3. Scoring each zone by number of touches + recency
"""
import numpy as np
import pandas as pd


def _find_pivots(series: pd.Series, window: int = 10) -> list[float]:
    """Return list of pivot prices (both highs and lows)."""
    pivots = []
    for i in range(window, len(series) - window):
        seg = series.iloc[i - window: i + window + 1]
        if series.iloc[i] == seg.max() or series.iloc[i] == seg.min():
            pivots.append(float(series.iloc[i]))
    return pivots


def _cluster_pivots(
    pivots: list[float],
    current_price: float,
    tolerance_pct: float = 0.002,
) -> list[dict]:
    """Group nearby pivots into zones, return list of {level, touches, distance_pct}."""
    if not pivots:
        return []

    sorted_pivots = sorted(pivots)
    zones = []
    current_zone = [sorted_pivots[0]]

    for p in sorted_pivots[1:]:
        if (p - current_zone[-1]) / current_zone[-1] < tolerance_pct:
            current_zone.append(p)
        else:
            level = float(np.mean(current_zone))
            zones.append({
                "level":        level,
                "touches":      len(current_zone),
                "distance_pct": abs(level - current_price) / current_price,
            })
            current_zone = [p]

    level = float(np.mean(current_zone))
    zones.append({
        "level":        level,
        "touches":      len(current_zone),
        "distance_pct": abs(level - current_price) / current_price,
    })
    return zones


def compute_sr_score(
    df: pd.DataFrame,
    direction: str | None = None,
    pivot_window: int = 10,
    tolerance_pct: float = 0.002,
    proximity_pct: float = 0.003,
) -> tuple[float, float | None]:
    """
    Returns (score: 0.0-1.0, nearest_level).

    score = 0.0 if price is not near any significant S/R level, or if the
    nearest level is acting *against* the signal direction.

    Direction-aware logic:
    - LONG:  positive score only when price is near a *support* level
             (level is below current price — we're bouncing off it).
    - SHORT: positive score only when price is near a *resistance* level
             (level is above current price — we're rejecting from it).
    - None:  direction-agnostic (legacy, scores all nearby levels).

    Without this, price pressing into resistance while going LONG would get
    the same sr_score as price bouncing off support — adding noise rather
    than edge to the confluence signal.
    """
    if len(df) < pivot_window * 3:
        return 0.0, None

    current_price = float(df["close"].iloc[-1])
    pivots = _find_pivots(df["close"], pivot_window)
    zones = _cluster_pivots(pivots, current_price, tolerance_pct)

    # Filter zones within proximity of current price
    nearby = [z for z in zones if z["distance_pct"] <= proximity_pct]

    if not nearby:
        return 0.0, None

    # Direction filter: only keep levels that support the trade direction.
    if direction == "LONG":
        # Support = level below price (price bouncing up off it)
        nearby = [z for z in nearby if z["level"] <= current_price]
    elif direction == "SHORT":
        # Resistance = level above price (price rejecting down from it)
        nearby = [z for z in nearby if z["level"] >= current_price]

    if not nearby:
        return 0.0, None

    # Score: touches (capped at 5) × proximity bonus
    best = max(nearby, key=lambda z: z["touches"])
    touches_score   = min(best["touches"], 5) / 5.0
    proximity_score = 1.0 - (best["distance_pct"] / proximity_pct)

    score = (touches_score * 0.6 + proximity_score * 0.4)
    return round(score, 4), best["level"]
