"""
Economic Surprise Signal.

Measures whether recent high-impact data releases surprised consensus
expectations. A positive surprise for the base currency (or negative for
the quote) confirms the trade direction.

Architecture:
  - Scheduler task `update_economic_surprise` (runs every 30 min) queries the
    DB for recent HIGH-impact releases with actual + forecast populated, computes
    per-currency surprise scores, and caches them in Redis.
  - `get_surprise_score()` reads the cached score for a given instrument/direction.

Score semantics:
  - 1.0 → all recent releases beat consensus in the trade direction
  - 0.5 → neutral (no data, mixed surprises, or equal offsetting signals)
  - 0.0 → all recent releases missed consensus against the trade direction

Fail-open: returns 0.5 when Redis or DB data is unavailable.
"""
from __future__ import annotations

import json
import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

REDIS_KEY_PREFIX = "econ_surprise"
REDIS_TTL_SECONDS = 3600  # 1 hour — scheduler refreshes every 30 min

# Instrument → (base_currency, quote_currency)
_PAIR_CURRENCIES: dict[str, tuple[str, str]] = {
    "EUR_USD": ("EUR", "USD"),
    "GBP_USD": ("GBP", "USD"),
    "USD_JPY": ("USD", "JPY"),
    "USD_CHF": ("USD", "CHF"),
    "AUD_USD": ("AUD", "USD"),
    "NZD_USD": ("NZD", "USD"),
    "USD_CAD": ("USD", "CAD"),
    "EUR_JPY": ("EUR", "JPY"),
    "GBP_JPY": ("GBP", "JPY"),
}


def _parse_value(s: str | None) -> float | None:
    """
    Parse ForexFactory numeric strings to float.
    Handles K/M/B suffixes and % signs.
    Returns None on failure (treated as no data).
    """
    if not s:
        return None
    s = s.strip().replace(",", "")
    multiplier = 1.0
    if s.endswith("K"):
        multiplier = 1e3
        s = s[:-1]
    elif s.endswith("M"):
        multiplier = 1e6
        s = s[:-1]
    elif s.endswith("B"):
        multiplier = 1e9
        s = s[:-1]
    s = s.rstrip("%")
    # Strip any remaining non-numeric chars except . and -
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        return float(s) * multiplier
    except ValueError:
        return None


def _surprise_sign(actual: str | None, forecast: str | None) -> float | None:
    """
    Returns +1.0 if actual beat forecast, -1.0 if missed, None if unparseable.
    """
    a = _parse_value(actual)
    f = _parse_value(forecast)
    if a is None or f is None:
        return None
    if abs(a - f) < 1e-10:
        return 0.0
    return 1.0 if a > f else -1.0


async def compute_currency_surprise(
    currency: str,
    calendar_repo: Any,
    lookback: int = 3,
) -> float:
    """
    Query the last `lookback` HIGH-impact releases for `currency` where actual
    is recorded and compute a mean surprise score in [-1, 1].

    Returns 0.0 (neutral) if no data is available.
    """
    events = await calendar_repo.get_recent_releases(
        currencies=[currency], limit=lookback
    )
    signs = []
    for evt in events:
        s = _surprise_sign(evt.actual, evt.forecast)
        if s is not None:
            signs.append(s)

    if not signs:
        return 0.0
    return sum(signs) / len(signs)  # [-1, 1]


async def update_surprise_cache(redis_client: Any, calendar_repo: Any) -> None:
    """
    Called by the scheduler every 30 min. Computes surprise scores for all
    tracked currencies and writes them to Redis.
    """
    currencies = {"EUR", "USD", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"}
    for ccy in currencies:
        try:
            score = await compute_currency_surprise(ccy, calendar_repo)
            key = f"{REDIS_KEY_PREFIX}:{ccy}"
            await redis_client.setex(key, REDIS_TTL_SECONDS, json.dumps(score))
        except Exception as exc:
            logger.warning("econ_surprise_update_failed", currency=ccy, error=str(exc))


async def get_surprise_score(
    redis_client: Any,
    instrument: str,
    direction: str,
) -> float:
    """
    Returns a confluence component score in [0.0, 1.0].

    Logic:
      net_surprise = base_surprise - quote_surprise  ([-2, 2])
      If direction == LONG:  score = 0.5 + net_surprise * 0.25  (→ [0, 1])
      If direction == SHORT: score = 0.5 - net_surprise * 0.25  (→ [0, 1])

    Fail-open: returns 0.5 when data is unavailable.
    """
    pair = _PAIR_CURRENCIES.get(instrument)
    if pair is None:
        return 0.5

    base_ccy, quote_ccy = pair
    try:
        base_raw  = await redis_client.get(f"{REDIS_KEY_PREFIX}:{base_ccy}")
        quote_raw = await redis_client.get(f"{REDIS_KEY_PREFIX}:{quote_ccy}")

        base_score  = json.loads(base_raw)  if base_raw  else 0.0
        quote_score = json.loads(quote_raw) if quote_raw else 0.0

        net = float(base_score) - float(quote_score)  # [-2, 2]
        if direction == "LONG":
            raw = 0.5 + net * 0.25
        else:
            raw = 0.5 - net * 0.25

        return max(0.0, min(1.0, raw))

    except Exception as exc:
        logger.warning("econ_surprise_read_failed", instrument=instrument, error=str(exc))
        return 0.5
