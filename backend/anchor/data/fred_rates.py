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
}

# Instrument → (base_currency, quote_currency)
_INSTRUMENT_CURRENCIES: Dict[str, tuple] = {
    "EUR_USD": ("EUR", "USD"),
    "GBP_USD": ("GBP", "USD"),
    "USD_JPY": ("USD", "JPY"),
    "AUD_USD": ("AUD", "USD"),
    "USD_CAD": ("USD", "CAD"),
}

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

    async def fetch_latest_rate(self, series_id: str) -> Optional[float]:
        """Fetch the most recent observation for a FRED series."""
        assert self._client is not None
        params = {
            "series_id":      series_id,
            "api_key":        self._api_key,
            "file_type":      "json",
            "sort_order":     "desc",
            "limit":          "5",       # grab last 5 in case latest is "."
            "observation_start": "2020-01-01",
        }
        try:
            resp = await self._client.get(FRED_BASE, params=params)
            resp.raise_for_status()
            observations = resp.json().get("observations", [])
            for obs in observations:
                val = obs.get("value", ".")
                if val != ".":
                    return float(val)
        except Exception as exc:
            logger.warning("fred_fetch_failed", series=series_id, error=str(exc))
        return None

    async def fetch_all_rates(self) -> Dict[str, Optional[float]]:
        """Return {currency: rate_pct} for all tracked currencies."""
        rates: Dict[str, Optional[float]] = {}
        for currency, series_id in _RATE_SERIES.items():
            rate = await self.fetch_latest_rate(series_id)
            rates[currency] = rate
            logger.debug("fred_rate", currency=currency, series=series_id, rate=rate)
        return rates

    @staticmethod
    def compute_differentials(rates: Dict[str, Optional[float]]) -> Dict[str, Dict]:
        """Compute rate differential per instrument from raw currency rates.

        Returns:
            {instrument: {"rate_diff": float, "base_rate": float,
                          "quote_rate": float, "available": bool}}
        """
        result: Dict[str, Dict] = {}
        for instrument, (base, quote) in _INSTRUMENT_CURRENCIES.items():
            base_rate  = rates.get(base)
            quote_rate = rates.get(quote)
            if base_rate is not None and quote_rate is not None:
                diff = base_rate - quote_rate
                result[instrument] = {
                    "rate_diff":  round(diff, 4),
                    "base_rate":  round(base_rate, 4),
                    "quote_rate": round(quote_rate, 4),
                    "available":  True,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            else:
                result[instrument] = {
                    "rate_diff":  0.0,
                    "base_rate":  base_rate,
                    "quote_rate": quote_rate,
                    "available":  False,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
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
