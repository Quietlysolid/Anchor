"""
VIX Position-Size Multiplier.

VIX (CBOE Volatility Index) measures implied volatility of S&P 500 options.
It has a strong positive correlation with FX volatility across major pairs —
when equity fear spikes, spreads widen, slippage increases, and edge collapses.

This module does NOT vote on trade direction. It returns a scalar multiplier
[0.25, 1.0] that the position sizer applies to the calculated unit size:

  VIX < 15   → calm market  → full size  (1.00)
  VIX 15–20  → normal range → full size  (1.00)
  VIX 20–25  → elevated     → 75% size   (0.75)
  VIX 25–30  → high         → 50% size   (0.50)
  VIX > 30   → panic        → 25% size   (0.25)

FRED series: VIXCLS (daily closing VIX from CBOE, free, no auth needed)
  — Published by FRED: https://fred.stlouisfed.org/series/VIXCLS
  — Updates daily (Mon–Fri, US market hours)

Redis key: vix_latest
TTL: 4 hours (VIX changes daily; intraday we just need one fresh read)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
_VIX_SERIES = "VIXCLS"
_REDIS_KEY  = "vix_latest"
_TTL_SECS   = 4 * 3_600  # 4 hours


# VIX → position size multiplier lookup
# Linear interpolation between breakpoints
_BREAKPOINTS = [
    (0.0,  1.00),
    (20.0, 1.00),
    (25.0, 0.75),
    (30.0, 0.50),
    (40.0, 0.25),
    (999., 0.25),
]


def vix_size_multiplier(vix: Optional[float]) -> float:
    """Return position-size multiplier given current VIX.

    Returns 1.0 (no reduction) if VIX is unavailable — fail open so a FRED
    outage doesn't silently halt trading. The caller may log the absence.
    """
    if vix is None or vix <= 0:
        return 1.0

    for i in range(len(_BREAKPOINTS) - 1):
        lo_vix, lo_mult = _BREAKPOINTS[i]
        hi_vix, hi_mult = _BREAKPOINTS[i + 1]
        if lo_vix <= vix < hi_vix:
            t = (vix - lo_vix) / (hi_vix - lo_vix)
            return round(lo_mult + t * (hi_mult - lo_mult), 4)

    return _BREAKPOINTS[-1][1]


async def fetch_vix(api_key: str) -> Optional[float]:
    """Fetch latest VIX from FRED. Returns float or None on error."""
    params = {
        "series_id":         _VIX_SERIES,
        "api_key":           api_key,
        "file_type":         "json",
        "sort_order":        "desc",
        "limit":             "5",
        "observation_start": "2020-01-01",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(FRED_BASE, params=params)
            resp.raise_for_status()
            observations = resp.json().get("observations", [])
            for obs in observations:
                val = obs.get("value", ".")
                if val != ".":
                    return float(val)
    except Exception as exc:
        logger.warning("vix_fetch_failed", error=str(exc))
    return None


async def fetch_and_store(redis_client, api_key: str) -> Optional[float]:
    """Fetch VIX from FRED, cache in Redis. Returns VIX float or None."""
    vix = await fetch_vix(api_key)

    payload = {
        "vix":        vix,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis_client.set(_REDIS_KEY, json.dumps(payload), ex=_TTL_SECS)

    logger.info("vix_stored", vix=vix, multiplier=vix_size_multiplier(vix))
    return vix


async def get_cached_multiplier(redis_client) -> float:
    """Return cached VIX size multiplier, or 1.0 if cache is empty."""
    raw = await redis_client.get(_REDIS_KEY)
    if not raw:
        logger.debug("vix_cache_miss_using_full_size")
        return 1.0
    try:
        data = json.loads(raw)
        return vix_size_multiplier(data.get("vix"))
    except Exception:
        return 1.0


async def get_cached_vix(redis_client) -> Optional[float]:
    """Return the cached raw VIX value, or None."""
    raw = await redis_client.get(_REDIS_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw).get("vix")
    except Exception:
        return None
