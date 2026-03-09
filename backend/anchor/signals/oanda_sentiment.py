"""
OANDA Position Book Sentiment Signal.

Uses OANDA's free /positionBook endpoint to read the net long/short exposure
of all retail traders at the current price bucket.

Contrarian logic (standard in FX): when the crowd is heavily long, fading
them (going SHORT) has an edge because:
  1. Longs are already in — buying pressure is exhausted.
  2. Their stops cluster just below support — a dip accelerates via stop-out.
  3. Retail is systematically wrong at extremes (per Sentix, OANDA, IG data).

Score convention (returned value in [-1, 1]):
  +1.0  → crowd is heavily SHORT  → bullish contrarian signal  (favour LONG)
  -1.0  → crowd is heavily LONG   → bearish contrarian signal  (favour SHORT)
   0.0  → balanced book           → no edge

`sentiment_signal_score(instrument, direction, cached)` maps the raw score
to a [0, 1] component weight for the confluence engine:
  0.0   → sentiment opposes the proposed direction
  0.5   → neutral (balanced book or data unavailable)
  1.0   → sentiment strongly supports the proposed direction

Redis key: oanda_sentiment:{instrument}
TTL: 5 minutes (position book refreshes every few seconds on OANDA, but
     we only need a fresh read each signal bar)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import httpx
import structlog

logger = structlog.get_logger(__name__)

# OANDA instrument names match ours (EUR_USD, GBP_USD …)
_POSBOOK_PATH = "/v3/instruments/{instrument}/positionBook"

# Crowd bias above this threshold triggers a contrarian signal
_BIAS_THRESHOLD = 0.55  # 55/45 split → signal starts; 70/30 → max signal

# Weight sentimentment when the crowd is clearly on one side
_STRONG_BIAS = 0.70  # ≥70% one-sided → score = 1.0


def _contrarian_score(long_pct: float) -> float:
    """Convert net long% to a contrarian score in [-1, 1].

    long_pct: fraction of open positions that are long (0.0 – 1.0)
    Returns:
      +1.0 if crowd is max short (contrarian LONG)
      -1.0 if crowd is max long  (contrarian SHORT)
    """
    short_pct = 1.0 - long_pct
    net = long_pct - short_pct  # net ∈ [-1, 1]; +1 = all long, -1 = all short

    # Magnitude: how extreme is the imbalance?
    magnitude = abs(net)
    if magnitude < (_BIAS_THRESHOLD * 2 - 1):  # below weak threshold → neutral
        return 0.0

    # Clamp and scale: magnitude 0→0 at threshold, 1→1 at full one-sidedness
    threshold_net = _BIAS_THRESHOLD * 2 - 1  # 0.10 for 55%
    strong_net = _STRONG_BIAS * 2 - 1         # 0.40 for 70%
    normalised = min(1.0, (magnitude - threshold_net) / max(strong_net - threshold_net, 1e-9))

    # Contrarian: crowd long → return negative (SHORT signal)
    return -normalised * (1 if net > 0 else -1)


def sentiment_signal_score(
    raw_score: Optional[float],
    direction: str,
) -> float:
    """Map contrarian score to [0, 1] component weight.

    raw_score: output of _contrarian_score, or None if data unavailable
    direction: 'LONG' or 'SHORT'
    """
    if raw_score is None:
        return 0.5  # neutral — no data, don't penalise

    # raw_score > 0 → contrarian signal is LONG
    # raw_score < 0 → contrarian signal is SHORT
    if direction == "LONG":
        agree = raw_score > 0
        strength = abs(raw_score)
    else:  # SHORT
        agree = raw_score < 0
        strength = abs(raw_score)

    if not agree and strength > 0:
        # Sentiment actively opposes direction
        return max(0.0, 0.5 - strength * 0.5)

    # Sentiment supports direction
    return 0.5 + strength * 0.5


class OandaSentimentFetcher:
    """Async fetcher for OANDA positionBook data."""

    def __init__(self, api_key: str, base_url: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        }
        self._base_url = base_url.rstrip("/")

    async def fetch(self, instrument: str) -> Optional[Dict]:
        """Return raw positionBook response dict, or None on error."""
        url = self._base_url + _POSBOOK_PATH.format(instrument=instrument)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=self._headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning(
                "oanda_positionbook_failed",
                instrument=instrument,
                error=str(exc),
            )
            return None

    async def get_long_pct(self, instrument: str) -> Optional[float]:
        """Return fraction of open positions that are long, or None."""
        data = await self.fetch(instrument)
        if not data:
            return None

        buckets = data.get("positionBook", {}).get("buckets", [])
        if not buckets:
            return None

        total_long  = 0.0
        total_short = 0.0
        for bucket in buckets:
            total_long  += abs(float(bucket.get("longCountPercent",  0)))
            total_short += abs(float(bucket.get("shortCountPercent", 0)))

        total = total_long + total_short
        if total < 1e-9:
            return None

        return total_long / total


async def fetch_and_store(
    redis_client,
    instrument: str,
    api_key: str,
    base_url: str,
) -> Optional[float]:
    """Fetch sentiment for one instrument, store in Redis. Returns long_pct."""
    fetcher = OandaSentimentFetcher(api_key, base_url)
    long_pct = await fetcher.get_long_pct(instrument)

    payload = {
        "long_pct":   long_pct,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    redis_key = f"oanda_sentiment:{instrument}"
    await redis_client.set(redis_key, json.dumps(payload), ex=5 * 60)  # 5-min TTL

    logger.debug(
        "oanda_sentiment_stored",
        instrument=instrument,
        long_pct=long_pct,
    )
    return long_pct


async def get_cached_score(
    redis_client,
    instrument: str,
) -> Optional[float]:
    """Return cached contrarian score for instrument, or None."""
    raw = await redis_client.get(f"oanda_sentiment:{instrument}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        long_pct = data.get("long_pct")
        if long_pct is None:
            return None
        return _contrarian_score(float(long_pct))
    except Exception:
        return None
