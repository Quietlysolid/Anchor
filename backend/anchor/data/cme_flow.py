"""
CME FX Futures Flow — Open Interest + Volume Signal.

Institutional money moves through CME FX futures (not the spot market).
By tracking Open Interest and Volume on the equivalent futures contracts we
can infer whether institutions are building or unwinding positions.

Signal logic (volume + price momentum):
  Volume rising  + price rising  → institutional buying   → confirms LONG
  Volume rising  + price falling → institutional selling  → confirms SHORT
  Volume falling + price moving  → trend thinning out     → weakening signal
  Volume spike vs 20-day avg    → big-money event         → amplify score

  Note: yfinance does not expose raw CME Open Interest through history().
  Volume trend is used as the OI proxy — rising volume on a directional
  move closely tracks institutional commitment in the futures market.

CME tickers used:
  EUR_USD → 6E=F  (Euro FX futures, 125,000 EUR/contract)
  GBP_USD → 6B=F  (British Pound futures, 62,500 GBP/contract)
  USD_JPY → 6J=F  (Japanese Yen futures — LONG 6J = LONG JPY = SHORT USD/JPY,
                   so signal is inverted for USD_JPY)

Score 0.0–1.0:
  1.0 → strong OI + volume confirmation of signal direction
  0.5 → neutral / data unavailable (fail-open)
  0.0 → OI + volume contradict signal direction

Redis key: cme_flow:{instrument}
TTL: 2 hours (CME daily data refreshes during market hours; 2h is a
     reasonable stale limit while avoiding excessive yfinance calls).

Requires: yfinance (pip install yfinance)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# CME futures ticker per instrument.
# inverted=True → the futures contract is LONG the quote currency,
# so a bullish futures position means bearish spot (e.g. LONG 6J = SHORT USD/JPY).
# None ticker → composite pair (EUR_JPY = 6E + 6J combined).
_CME_MAP: dict[str, tuple[str | None, bool]] = {
    "EUR_USD": ("6E=F", False),
    "GBP_USD": ("6B=F", False),
    "USD_JPY": ("6J=F", True),
    "NZD_USD": ("6N=F", False),   # New Zealand Dollar futures
    "AUD_USD": ("6A=F", False),   # Australian Dollar futures
    "USD_CAD": ("6C=F", True),    # Canadian Dollar futures (inverted: long CAD = short USD/CAD)
    "EUR_JPY": (None,   False),   # Composite: derived from 6E + 6J, see _compute_eur_jpy_composite
}

_OI_LOOKBACK = 20   # days for rolling average / trend slope
_VOL_LOOKBACK = 20  # days for average daily volume


def _compute_flow_score(ticker_sym: str, inverted: bool) -> Optional[dict]:
    """Fetch CME futures data and return a flow-score dict, or None on error.

    Returns:
        {
            "raw_score":     float in [-1, 1],
            "oi_trend":      "INCREASING" | "DECREASING" | "FLAT",
            "vol_ratio":     float,     # today / 20-day avg
            "price_trend":   "UP" | "DOWN" | "FLAT",
            "oi_last":       int,
            "vol_last":      int,
        }
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance_not_installed", note="pip install yfinance to enable CME flow signal")
        return None

    try:
        ticker  = yf.Ticker(ticker_sym)
        # Fetch 60 calendar days to ensure ≥20 trading days
        hist    = ticker.history(period="60d", auto_adjust=True)

        if hist is None or len(hist) < _OI_LOOKBACK + 2:
            logger.debug("cme_insufficient_history", ticker=ticker_sym, rows=len(hist) if hist is not None else 0)
            return None

        # Trim to last 30 trading days for calculations
        hist = hist.tail(30)

        # ── Price trend (5-day momentum) ──────────────────────────────
        closes     = hist["Close"].dropna()
        if len(closes) < 5:
            return None
        price_ret  = (float(closes.iloc[-1]) - float(closes.iloc[-5])) / float(closes.iloc[-5])
        if price_ret >  0.002:
            price_trend = "UP"
        elif price_ret < -0.002:
            price_trend = "DOWN"
        else:
            price_trend = "FLAT"

        # ── Volume trend ──────────────────────────────────────────────
        # Note: yfinance history() does not expose CME Open Interest.
        # We use volume trend as a proxy: rising volume on a directional
        # move = institutional participation; falling volume = thinning out.
        vol_series   = hist["Volume"].dropna()
        vol_last     = int(vol_series.iloc[-1]) if len(vol_series) >= 1 else 0
        vol_avg_long = float(vol_series.tail(_VOL_LOOKBACK).mean()) if len(vol_series) >= 2 else 0.0
        vol_avg_short= float(vol_series.tail(5).mean()) if len(vol_series) >= 5 else vol_avg_long
        vol_ratio    = (vol_last / vol_avg_long) if vol_avg_long > 0 else 1.0

        # Volume trend: recent 5-day avg vs 20-day avg
        if vol_avg_long > 0:
            vol_trend_ratio = vol_avg_short / vol_avg_long
            if vol_trend_ratio > 1.10:
                vol_trend = "INCREASING"
            elif vol_trend_ratio < 0.90:
                vol_trend = "DECREASING"
            else:
                vol_trend = "FLAT"
        else:
            vol_trend = "FLAT"

        # ── Combine into a directional raw score [-1, 1] ─────────────
        # Rising volume + directional price move = institutional participation
        # Falling volume + directional move = thinning, trend weakening
        if vol_trend == "INCREASING":
            if price_trend == "UP":
                base = +0.7   # volume + price up → institutional buying
            elif price_trend == "DOWN":
                base = -0.7   # volume + price down → institutional selling
            else:
                base = 0.0
        elif vol_trend == "DECREASING":
            # Volume falling — trend potentially exhausting; slight fade
            if price_trend == "UP":
                base = -0.2
            elif price_trend == "DOWN":
                base = +0.2
            else:
                base = 0.0
        else:
            # Flat volume — no strong institutional signal
            if price_trend == "UP":
                base = +0.3
            elif price_trend == "DOWN":
                base = -0.3
            else:
                base = 0.0

        # Today's volume spike amplifier (covers news / major macro events)
        vol_mult  = min(1.4, 1.0 + max(0.0, vol_ratio - 1.0) * 0.2)
        raw_score = max(-1.0, min(1.0, base * vol_mult))

        # Invert for USD_JPY (6J is JPY/USD, opposite direction to spot)
        if inverted:
            raw_score = -raw_score
            if price_trend == "UP":
                price_trend = "DOWN"
            elif price_trend == "DOWN":
                price_trend = "UP"

        return {
            "raw_score":   round(raw_score, 4),
            "vol_trend":   vol_trend,
            "vol_ratio":   round(vol_ratio, 3),
            "price_trend": price_trend,
            "vol_last":    vol_last,
        }

    except Exception as exc:
        logger.warning("cme_flow_compute_failed", ticker=ticker_sym, error=str(exc))
        return None


def _compute_eur_jpy_composite() -> Optional[dict]:
    """Derive EUR_JPY CME flow from 6E (EUR futures) + 6J (JPY futures).

    EUR_JPY LONG = EUR strengthening + JPY weakening.
      6E raw > 0  → EUR futures bullish → confirms EUR_JPY LONG
      6J raw < 0  → JPY futures bearish → confirms EUR_JPY LONG (JPY weak)
    Composite raw = (6E_raw - 6J_raw) / 2  → maps to [-1, 1]
    """
    eur = _compute_flow_score("6E=F", inverted=False)
    jpy = _compute_flow_score("6J=F", inverted=False)  # raw JPY direction, not inverted
    if eur is None or jpy is None:
        return None

    eur_raw = eur["raw_score"]
    jpy_raw = jpy["raw_score"]
    composite = max(-1.0, min(1.0, (eur_raw - jpy_raw) / 2.0))

    up   = composite > 0.1
    down = composite < -0.1
    return {
        "raw_score":   round(composite, 4),
        "vol_trend":   eur.get("vol_trend", "FLAT"),
        "vol_ratio":   round((eur.get("vol_ratio", 1.0) + jpy.get("vol_ratio", 1.0)) / 2.0, 3),
        "price_trend": "UP" if up else ("DOWN" if down else "FLAT"),
        "vol_last":    0,
        "eur_raw":     eur_raw,
        "jpy_raw":     jpy_raw,
    }


def cme_flow_signal_score(raw_score: Optional[float], direction: str) -> float:
    """Map CME flow raw score to [0, 1] component weight."""
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


async def fetch_and_store(redis_client, instrument: str) -> Optional[dict]:
    """Compute CME flow for one instrument and store in Redis."""
    mapping = _CME_MAP.get(instrument)
    if not mapping:
        return None

    ticker_sym, inverted = mapping
    if ticker_sym is None:
        # Composite instrument (EUR_JPY)
        result = _compute_eur_jpy_composite()
    else:
        result = _compute_flow_score(ticker_sym, inverted)

    payload = {
        "result":     result,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    key = f"cme_flow:{instrument}"
    await redis_client.set(key, json.dumps(payload), ex=2 * 3_600)

    logger.debug(
        "cme_flow_stored",
        instrument=instrument,
        raw_score=result.get("raw_score") if result else None,
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
        raw = await redis_client.get(f"cme_flow:{instrument}")
        if not raw:
            return 0.5, None

        payload = json.loads(raw)
        result  = payload.get("result")
        if not result:
            return 0.5, None

        score = cme_flow_signal_score(result.get("raw_score"), direction)
        return score, result

    except Exception as exc:
        logger.warning("cme_flow_cache_failed", instrument=instrument, error=str(exc))
        return 0.5, None
