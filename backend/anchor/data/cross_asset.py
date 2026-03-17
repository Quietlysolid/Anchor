"""
Cross-Asset Risk Sentiment Signal.

Institutional capital flows between risk assets and safe havens based on
macro regime (risk-on vs risk-off). This cross-asset dynamic is one of the
strongest drivers of FX moves that pure price-based signals miss entirely.

Signal logic:
  SPY (S&P 500 ETF)  → equity risk appetite proxy
  GLD (SPDR Gold)    → safe-haven demand proxy

  risk_sentiment = 0.60 * spy_momentum + 0.40 * (-gld_momentum)
    +1.0 = strong risk-on  (SPY up, gold flat/down)
    -1.0 = strong risk-off (SPY down, gold up)

Currency sensitivity map:
  AUD_USD  +1.0  — high-beta commodity/risk currency
  NZD_USD  +1.0  — high-beta commodity/risk currency
  USD_CAD  -0.8  — CAD is risk currency; USD/CAD falls in risk-on
  EUR_JPY  +0.9  — JPY is the primary safe haven; EUR/JPY surges in risk-on
  EUR_USD  +0.4  — moderate: EUR has mild risk-on correlation vs USD
  GBP_USD  +0.4  — moderate: GBP follows global risk appetite

Score 0.0–1.0:
  1.0 → strong cross-asset regime confirmation of signal direction
  0.5 → neutral / data unavailable (fail-open)
  0.0 → cross-asset regime opposes signal direction

Redis key: cross_asset_risk
TTL: 2 hours (SPY/GLD are daily data; 2h prevents excessive yfinance calls
     while staying relevant through the trading session).

Requires: yfinance (pip install yfinance)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# How much each instrument's spot rate is driven by risk-on sentiment.
# +1.0 = instrument goes UP in risk-on, DOWN in risk-off.
# -1.0 = instrument goes DOWN in risk-on (USD-denominated safe-haven).
_RISK_SENSITIVITY: dict[str, float] = {
    "AUD_USD": +1.0,
    "NZD_USD": +1.0,
    "USD_CAD": -0.8,   # CAD strengthens in risk-on → USD/CAD falls
    "EUR_JPY": +0.9,   # JPY safe-haven → EUR/JPY surges in risk-on
    "EUR_USD": +0.4,
    "GBP_USD": +0.4,
}

# Normalisation: ±3% 5-day return maps to ±1.0 sentiment score.
# SPY moves ~1-2% on typical risk-on/off days; 3% captures elevated moves.
_RETURN_SCALE = 0.03


def _compute_risk_sentiment() -> Optional[dict]:
    """Fetch SPY + GLD via yfinance and compute risk sentiment score.

    Returns:
        {
            "risk_sentiment": float in [-1, 1],
            "spy_ret_5d":     float,
            "gld_ret_5d":     float,
            "regime":         "RISK_ON" | "RISK_OFF" | "NEUTRAL",
        }
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance_not_installed", note="pip install yfinance for cross_asset signal")
        return None

    try:
        spy_hist = yf.Ticker("SPY").history(period="15d", auto_adjust=True)
        gld_hist = yf.Ticker("GLD").history(period="15d", auto_adjust=True)

        if spy_hist is None or len(spy_hist) < 6:
            return None
        if gld_hist is None or len(gld_hist) < 6:
            return None

        spy_closes = spy_hist["Close"].dropna()
        gld_closes = gld_hist["Close"].dropna()

        if len(spy_closes) < 6 or len(gld_closes) < 6:
            return None

        # 5-day momentum (last 5 trading days ≈ 1 week)
        spy_ret = (float(spy_closes.iloc[-1]) - float(spy_closes.iloc[-6])) / float(spy_closes.iloc[-6])
        gld_ret = (float(gld_closes.iloc[-1]) - float(gld_closes.iloc[-6])) / float(gld_closes.iloc[-6])

        spy_norm = max(-1.0, min(1.0, spy_ret / _RETURN_SCALE))
        gld_norm = max(-1.0, min(1.0, gld_ret / _RETURN_SCALE))

        # Risk-on: SPY up + GLD flat/down. GLD is inverted (safe haven = anti-risk).
        sentiment = 0.60 * spy_norm - 0.40 * gld_norm
        sentiment = max(-1.0, min(1.0, sentiment))

        if sentiment > 0.25:
            regime = "RISK_ON"
        elif sentiment < -0.25:
            regime = "RISK_OFF"
        else:
            regime = "NEUTRAL"

        return {
            "risk_sentiment": round(sentiment, 4),
            "spy_ret_5d":     round(spy_ret, 5),
            "gld_ret_5d":     round(gld_ret, 5),
            "regime":         regime,
        }

    except Exception as exc:
        logger.warning("cross_asset_compute_failed", error=str(exc))
        return None


def cross_asset_signal_score(sentiment: Optional[float], instrument: str, direction: str) -> float:
    """Map cross-asset risk sentiment to [0, 1] component score.

    Logic:
      sensitivity = instrument's risk-on sensitivity (from _RISK_SENSITIVITY)
      net = sentiment * sensitivity  →  [-1, 1]
        positive net = risk-on sentiment supports this instrument going up
        negative net = risk-off sentiment supports this instrument going down

      LONG:  score = 0.5 + net * 0.5
      SHORT: score = 0.5 - net * 0.5
    """
    if sentiment is None:
        return 0.5

    sensitivity = _RISK_SENSITIVITY.get(instrument, 0.0)
    if sensitivity == 0.0:
        return 0.5

    net = sentiment * sensitivity  # [-1, 1]
    if direction == "LONG":
        return max(0.0, min(1.0, 0.5 + net * 0.5))
    else:
        return max(0.0, min(1.0, 0.5 - net * 0.5))


async def fetch_and_store(redis_client) -> Optional[dict]:
    """Compute risk sentiment and cache in Redis.

    Redis key: cross_asset_risk  TTL: 2 hours.
    """
    result = _compute_risk_sentiment()
    payload = {
        "result":     result,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis_client.set("cross_asset_risk", json.dumps(payload), ex=2 * 3_600)
    logger.debug(
        "cross_asset_stored",
        regime=result.get("regime") if result else None,
        sentiment=result.get("risk_sentiment") if result else None,
    )
    return result


async def get_cached_score(
    redis_client,
    instrument: str,
    direction: str,
) -> tuple[float, Optional[dict]]:
    """Return (component_score [0,1], raw_result | None) from Redis cache."""
    if redis_client is None:
        return 0.5, None

    try:
        raw = await redis_client.get("cross_asset_risk")
        if not raw:
            return 0.5, None

        payload = json.loads(raw)
        result  = payload.get("result")
        if not result:
            return 0.5, None

        sentiment = result.get("risk_sentiment")
        score = cross_asset_signal_score(sentiment, instrument, direction)
        return score, result

    except Exception as exc:
        logger.warning("cross_asset_cache_failed", instrument=instrument, error=str(exc))
        return 0.5, None
