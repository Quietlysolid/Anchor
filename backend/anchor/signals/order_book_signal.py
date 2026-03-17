"""
OANDA Order Book Signal.

Uses OANDA's free /orderBook endpoint to read the distribution of pending
orders (stop-losses and limit entries) across price levels.

Edge source — liquidity magnet theory:
  Institutional desks look for where retail stops are clustered, then drive
  price through those levels to fill large orders.  Heavy pending-order density
  above the current price is the primary upside "magnet" (and vice versa).

  This is the same data market-makers use when they see order flow from their
  own clients; OANDA publishes an aggregated version freely.

How the signal works:
  1. Fetch the order book and identify the current-price bucket.
  2. Sum pending-order volume in the ±SCAN_PCT range above vs below.
  3. Score the asymmetry: more orders above → bullish (price gets pulled up
     to sweep them); more orders below → bearish.
  4. A "stop-hunt zone" flag fires when price is within HUNT_ZONE_PIPS of a
     dense cluster — logged to metadata so the engine can note the setup.

Score 0.0–1.0:
  1.0 → strong order-book bias in the signal direction
  0.5 → balanced book or data unavailable (fail-open)
  0.0 → order-book bias opposes the signal direction

Redis key: order_book:{instrument}
TTL: 5 minutes (order books update every few seconds; 5 min aligns with the
     signal scan cadence and avoids hammering the API).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)

_ORDERBOOK_PATH = "/v3/instruments/{instrument}/orderBook"

# What fraction of the instrument price range to scan on each side.
# 0.005 = ±0.5% of current price — captures a day's typical swing range.
_SCAN_PCT = 0.005

# Threshold: order-side must hold at least this fraction of scanned volume
# to trigger a non-neutral score.
_BIAS_THRESHOLD = 0.55   # 55/45 split starts scoring
_STRONG_BIAS    = 0.72   # 72/28 = max score

# Pip sizes for stop-hunt zone check
_PIP_SIZE = {
    "EUR_USD": 0.0001, "GBP_USD": 0.0001, "AUD_USD": 0.0001,
    "NZD_USD": 0.0001, "USD_CAD": 0.0001, "USD_CHF": 0.0001,
    "EUR_GBP": 0.0001, "USD_JPY": 0.01,   "GBP_JPY": 0.01,
    "EUR_JPY": 0.01,
}
_HUNT_ZONE_PIPS = 15   # within 15 pips of a cluster = stop-hunt zone


def _asymmetry_score(above_vol: float, below_vol: float) -> float:
    """Convert (above, below) volume to an asymmetry score in [-1, 1].

    +1.0  → all pending orders are above price  → bullish magnet
    -1.0  → all pending orders are below price  → bearish magnet
     0.0  → balanced / below threshold
    """
    total = above_vol + below_vol
    if total < 1e-9:
        return 0.0

    above_pct = above_vol / total
    below_pct = below_vol / total
    dominant   = max(above_pct, below_pct)
    is_bullish = above_pct >= below_pct

    if dominant < _BIAS_THRESHOLD:
        return 0.0

    strong_net   = _STRONG_BIAS - _BIAS_THRESHOLD
    normalised   = min(1.0, (dominant - _BIAS_THRESHOLD) / max(strong_net, 1e-9))
    return normalised if is_bullish else -normalised


def order_book_signal_score(raw_score: Optional[float], direction: str) -> float:
    """Map raw asymmetry score to a [0, 1] component weight.

    raw_score > 0 → bullish order book (more orders above)
    raw_score < 0 → bearish order book (more orders below)
    """
    if raw_score is None:
        return 0.5

    if direction == "LONG":
        agree    = raw_score > 0
        strength = abs(raw_score)
    else:
        agree    = raw_score < 0
        strength = abs(raw_score)

    if not agree and strength > 0:
        return max(0.0, 0.5 - strength * 0.5)
    return 0.5 + strength * 0.5


class OandaOrderBookFetcher:
    """Async fetcher for OANDA orderBook data."""

    def __init__(self, api_key: str, base_url: str) -> None:
        self._headers  = {"Authorization": f"Bearer {api_key}"}
        self._base_url = base_url.rstrip("/")

    async def fetch(self, instrument: str) -> Optional[dict]:
        url = self._base_url + _ORDERBOOK_PATH.format(instrument=instrument)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=self._headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("oanda_orderbook_failed", instrument=instrument, error=str(exc))
            return None

    async def analyse(self, instrument: str) -> Optional[dict]:
        """Return analysis dict or None.

        Keys: raw_score, above_vol, below_vol, current_price, hunt_zone_above,
              hunt_zone_below, hunt_zone_distance_pips
        """
        data = await self.fetch(instrument)
        if not data:
            return None

        book    = data.get("orderBook", {})
        buckets = book.get("buckets", [])
        if not buckets:
            return None

        try:
            current_price = float(book.get("price", 0))
        except (TypeError, ValueError):
            return None

        if current_price <= 0:
            return None

        scan_high = current_price * (1 + _SCAN_PCT)
        scan_low  = current_price * (1 - _SCAN_PCT)
        pip       = _PIP_SIZE.get(instrument, 0.0001)

        above_vol     = 0.0
        below_vol     = 0.0
        hunt_above    = False
        hunt_below    = False
        best_above_d  = float("inf")
        best_below_d  = float("inf")

        for bucket in buckets:
            try:
                price  = float(bucket["price"])
                # Total pending order volume at this level
                vol    = abs(float(bucket.get("longCountPercent",  0))) \
                       + abs(float(bucket.get("shortCountPercent", 0)))
            except (KeyError, TypeError, ValueError):
                continue

            if price <= scan_low or price >= scan_high:
                continue

            if price > current_price:
                above_vol += vol
                dist_pips  = (price - current_price) / pip
                if dist_pips < best_above_d:
                    best_above_d = dist_pips
                    if dist_pips <= _HUNT_ZONE_PIPS and vol > 0.5:
                        hunt_above = True
            else:
                below_vol += vol
                dist_pips  = (current_price - price) / pip
                if dist_pips < best_below_d:
                    best_below_d = dist_pips
                    if dist_pips <= _HUNT_ZONE_PIPS and vol > 0.5:
                        hunt_below = True

        raw_score = _asymmetry_score(above_vol, below_vol)
        return {
            "raw_score":             raw_score,
            "above_vol":             round(above_vol, 4),
            "below_vol":             round(below_vol, 4),
            "current_price":         current_price,
            "hunt_zone_above":       hunt_above,
            "hunt_zone_below":       hunt_below,
            "nearest_above_pips":    round(best_above_d, 1) if best_above_d < float("inf") else None,
            "nearest_below_pips":    round(best_below_d, 1) if best_below_d < float("inf") else None,
        }


async def fetch_and_store(
    redis_client,
    instrument: str,
    api_key: str,
    base_url: str,
) -> Optional[dict]:
    """Fetch order book for one instrument, store analysis in Redis."""
    fetcher  = OandaOrderBookFetcher(api_key, base_url)
    analysis = await fetcher.analyse(instrument)

    payload = {
        "analysis":   analysis,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    key = f"order_book:{instrument}"
    await redis_client.set(key, json.dumps(payload), ex=5 * 60)

    logger.debug(
        "order_book_stored",
        instrument=instrument,
        raw_score=analysis.get("raw_score") if analysis else None,
    )
    return analysis


async def get_cached_score(
    redis_client,
    instrument: str,
    direction: str,
) -> tuple[float, Optional[dict]]:
    """Return (component_score [0,1], raw_analysis | None) from Redis cache."""
    if redis_client is None:
        return 0.5, None

    try:
        raw = await redis_client.get(f"order_book:{instrument}")
        if not raw:
            return 0.5, None

        payload  = json.loads(raw)
        analysis = payload.get("analysis")
        if not analysis:
            return 0.5, None

        score = order_book_signal_score(analysis.get("raw_score"), direction)
        return score, analysis

    except Exception as exc:
        logger.warning("order_book_cache_failed", instrument=instrument, error=str(exc))
        return 0.5, None
