"""
FRED interest rate fetcher.

Fetches the latest central bank policy rates for each currency in our
instrument universe and computes the rate differential (base - quote).

Rate differential is the single strongest macro driver of FX direction:
  - Positive diff  → base currency expected to strengthen (carry inflow)
  - Negative diff  → base currency expected to weaken   (carry outflow)

FRED series used (monthly/daily, most recent observation):
  USD  → FEDFUNDS          (Fed Funds effective rate)
  EUR  → ECBDFR            (ECB deposit facility rate)
  GBP  → IUDSOIA           (BoE SONIA overnight rate)
  JPY  → IR3TIB01JPM156N   (3-month interbank rate Japan — current BoJ policy proxy)
  AUD  → IRSTCI01AUM156N   (RBA cash rate target — confirmed current)
  CAD  → IRSTCB01CAM156N   (OECD short-term rate — BoC overnight rate)

Redis key: fred_rate_diff
TTL: 25 hours (rates change at most once per meeting, ~6 weeks apart;
     daily refresh is more than sufficient)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# FRED series ID per currency code
# USD: Fed Funds effective rate
# EUR: ECB deposit facility rate
# GBP: BoE SONIA overnight rate
# JPY: IMF/OECD short-term rate for Japan (BoJ near-zero policy rate)
# AUD: OECD short-term interest rate for Australia (RBA cash rate target)
# CAD: OECD short-term interest rate for Canada (BoC overnight rate)
_RATE_SERIES: Dict[str, str] = {
    "USD": "FEDFUNDS",
    "EUR": "ECBDFR",
    "GBP": "IUDSOIA",
    "JPY": "IR3TIB01JPM156N",
    "AUD": "IRSTCI01AUM156N",
    "CAD": "IRSTCB01CAM156N",
    "NZD": "IRSTCI01NZM156N",   # OECD short-term rate New Zealand (RBNZ OCR proxy)
    "CHF": "IRSTCI01CHM156N",   # OECD short-term rate Switzerland (SNB policy proxy)
}

# Instrument → (base_currency, quote_currency)
_INSTRUMENT_CURRENCIES: Dict[str, tuple] = {
    "EUR_USD": ("EUR", "USD"),
    "GBP_USD": ("GBP", "USD"),
    "USD_JPY": ("USD", "JPY"),
    "AUD_USD": ("AUD", "USD"),
    "USD_CAD": ("USD", "CAD"),
    "NZD_USD": ("NZD", "USD"),
    "USD_CHF": ("USD", "CHF"),
    "EUR_GBP": ("EUR", "GBP"),
    "GBP_JPY": ("GBP", "JPY"),
    "EUR_JPY": ("EUR", "JPY"),
}

# DXY — FRED Trade Weighted USD Index (Broad Goods, daily)
_DXY_SERIES = "DTWEXBGS"

# Normalise raw rate differentials to roughly [-1, 1].
# Most FX rate diffs stay within ±10 percentage points.
_RATE_DIFF_SCALE = 10.0


class FredRateFetcher:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "FredRateFetcher":
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    async def fetch_rate_history(self, series_id: str, limit: int = 12) -> list[float]:
        """Fetch the last `limit` valid observations for a FRED series (descending)."""
        assert self._client is not None
        params = {
            "series_id":         series_id,
            "api_key":           self._api_key,
            "file_type":         "json",
            "sort_order":        "desc",
            "limit":             str(limit),
            "observation_start": "2023-01-01",
        }
        try:
            resp = await self._client.get(FRED_BASE, params=params)
            resp.raise_for_status()
            observations = resp.json().get("observations", [])
            return [
                float(obs["value"])
                for obs in observations
                if obs.get("value", ".") != "."
            ]
        except Exception as exc:
            logger.warning("fred_fetch_history_failed", series=series_id, error=str(exc))
        return []

    async def fetch_latest_rate(self, series_id: str) -> Optional[float]:
        """Fetch the most recent observation for a FRED series."""
        history = await self.fetch_rate_history(series_id, limit=5)
        return history[0] if history else None

    async def fetch_all_rates(self) -> Dict[str, Dict]:
        """Return {currency: {rate, velocity}} for all tracked currencies.

        velocity = change in policy rate over the last ~6 observations
        (≈6 months for monthly series, captures direction of CB policy).
        Normalised to [-1, 1] with a ±3 pp velocity cap.
        """
        rates: Dict[str, Dict] = {}
        for currency, series_id in _RATE_SERIES.items():
            history = await self.fetch_rate_history(series_id, limit=12)
            if not history:
                rates[currency] = None
                continue
            current = history[0]
            # velocity: current vs oldest available (up to 6 periods back)
            lookback = min(6, len(history) - 1)
            velocity = current - history[lookback] if lookback > 0 else 0.0
            rates[currency] = {"rate": current, "velocity": round(velocity, 4)}
            logger.debug("fred_rate", currency=currency, rate=current, velocity=velocity)
        return rates

    @staticmethod
    def compute_differentials(rates: Dict[str, Optional[Dict]]) -> Dict[str, Dict]:
        """Compute rate differential + velocity per instrument.

        Returns:
            {instrument: {
                "rate_diff":      float,   # carry: base_rate - quote_rate (pp)
                "velocity_diff":  float,   # velocity: base_velocity - quote_velocity (pp)
                "base_rate":      float,
                "quote_rate":     float,
                "available":      bool,
            }}
        """
        result: Dict[str, Dict] = {}
        for instrument, (base, quote) in _INSTRUMENT_CURRENCIES.items():
            base_data  = rates.get(base)
            quote_data = rates.get(quote)
            if base_data is not None and quote_data is not None:
                diff     = base_data["rate"]     - quote_data["rate"]
                vel_diff = base_data["velocity"] - quote_data["velocity"]
                result[instrument] = {
                    "rate_diff":     round(diff, 4),
                    "velocity_diff": round(vel_diff, 4),
                    "base_rate":     round(base_data["rate"], 4),
                    "quote_rate":    round(quote_data["rate"], 4),
                    "available":     True,
                    "fetched_at":    datetime.now(timezone.utc).isoformat(),
                }
            else:
                result[instrument] = {
                    "rate_diff":     0.0,
                    "velocity_diff": 0.0,
                    "base_rate":     None,
                    "quote_rate":    None,
                    "available":     False,
                    "fetched_at":    datetime.now(timezone.utc).isoformat(),
                }
        return result


async def fetch_and_store(redis_client, api_key: str) -> Dict[str, Dict]:
    """Fetch FRED rates, compute differentials, store in Redis. Returns result dict."""
    async with FredRateFetcher(api_key) as fetcher:
        rates = await fetcher.fetch_all_rates()

    differentials = FredRateFetcher.compute_differentials(rates)

    available = [k for k, v in differentials.items() if v["available"]]
    logger.info("fred_rates_fetched", available=available, total=len(differentials))

    await redis_client.set(
        "fred_rate_diff",
        json.dumps(differentials),
        ex=25 * 3_600,  # 25-hour TTL
    )
    return differentials


async def fetch_and_store_dxy(redis_client, api_key: str) -> Optional[Dict]:
    """Fetch DXY (Trade Weighted USD Index) from FRED, compute 5-day momentum, cache in Redis.

    Redis key: dxy_data
    Structure: {value, change_5d_pct, trend: UP|DOWN|NEUTRAL, fetched_at}
    Trend: UP if change > +0.5%, DOWN if < -0.5%, else NEUTRAL
    """
    try:
        async with FredRateFetcher(api_key) as fetcher:
            assert fetcher._client is not None
            params = {
                "series_id":         _DXY_SERIES,
                "api_key":           api_key,
                "file_type":         "json",
                "sort_order":        "desc",
                "limit":             "10",
                "observation_start": "2020-01-01",
            }
            resp = await fetcher._client.get(FRED_BASE, params=params)
            resp.raise_for_status()
            obs = [
                float(o["value"])
                for o in resp.json().get("observations", [])
                if o.get("value", ".") != "."
            ]

        if len(obs) < 2:
            return None

        current   = obs[0]   # most recent (desc order)
        prior_5d  = obs[min(4, len(obs) - 1)]
        change_5d = round((current - prior_5d) / prior_5d * 100, 3)

        trend = "UP" if change_5d > 0.5 else ("DOWN" if change_5d < -0.5 else "NEUTRAL")
        data  = {
            "value":         round(current, 3),
            "change_5d_pct": change_5d,
            "trend":         trend,
            "fetched_at":    datetime.now(timezone.utc).isoformat(),
        }
        await redis_client.set("dxy_data", json.dumps(data), ex=25 * 3_600)
        logger.info("dxy_stored", value=current, change_5d_pct=change_5d, trend=trend)
        return data
    except Exception as exc:
        logger.warning("dxy_fetch_failed", error=str(exc))
        return None


async def get_rate_divergence_score(
    redis_client,
    instrument: str,
    direction: str,
) -> float:
    """Return a rate-divergence + velocity confluence score [0.0, 1.0].

    Blends two signals:
      1. Carry (static level): base_rate - quote_rate → which CB pays more now
      2. Velocity (direction of change): base_velocity - quote_velocity →
         which CB is hiking/cutting, capturing STIR repricing before it's
         fully reflected in the spot rate (e.g. RBA hike surprise scenario).

    Score = 0.60 * carry_score + 0.40 * velocity_score

    Normalisation:
      carry:    ±_RATE_DIFF_SCALE (10 pp) → ±1.0
      velocity: ±3 pp over ~6 periods → ±1.0
    """
    _VEL_SCALE = 3.0  # pp over ~6 months; RBA hiked ~4pp in 2022–23

    if redis_client is None:
        return 0.5

    try:
        raw = await redis_client.get("fred_rate_diff")
        if not raw:
            return 0.5

        data = json.loads(raw)
        entry = data.get(instrument)
        if not entry or not entry.get("available"):
            return 0.5

        rate_diff = float(entry["rate_diff"])
        vel_diff  = float(entry.get("velocity_diff", 0.0))

        # Carry component
        carry_norm = max(-1.0, min(1.0, rate_diff / _RATE_DIFF_SCALE))
        if direction == "LONG":
            carry_score = 0.5 + carry_norm * 0.5
        else:
            carry_score = 0.5 - carry_norm * 0.5

        # Velocity component: positive vel_diff = base CB hiking faster than quote
        vel_norm = max(-1.0, min(1.0, vel_diff / _VEL_SCALE))
        if direction == "LONG":
            vel_score = 0.5 + vel_norm * 0.5
        else:
            vel_score = 0.5 - vel_norm * 0.5

        score = 0.60 * carry_score + 0.40 * vel_score
        return round(max(0.0, min(1.0, score)), 4)

    except Exception as exc:
        logger.warning("rate_divergence_score_failed", instrument=instrument, error=str(exc))
        return 0.5
