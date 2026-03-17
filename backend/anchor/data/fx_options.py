"""
FX Options Risk Reversal Signal (currency ETF proxy).

A risk reversal is the IV difference between an out-of-the-money call and an
equidistant OTM put.  When calls are more expensive than puts (positive RR),
the market is paying a premium to hedge or bet on upside — bullish bias.
When puts are more expensive (negative RR), the market expects downside.

This is a standard institutional tool: FX desks and hedge funds look at the
1-week and 1-month 25-delta risk reversal every morning before deciding on
direction bias.  The data is normally locked behind Bloomberg/Refinitiv.

Proxy approach (retail-accessible, free):
  We use listed options on liquid currency ETFs instead of interbank FX options.
  The ETFs track their underlying FX pairs closely:

    EUR_USD → FXE  (CurrencyShares Euro Trust)
    GBP_USD → FXB  (CurrencyShares British Pound Sterling Trust)
    USD_JPY → FXY  (CurrencyShares Japanese Yen Trust)
              NOTE: FXY is LONG JPY, so a bullish FXY RR = bearish USD/JPY.
              The signal is inverted for USD_JPY.

25-delta proxy:
  True 25-delta strikes require an options pricing model (Black-Scholes + vol
  surface).  We approximate by using strikes ~1 ATR above and below the current
  ETF price, which closely corresponds to 25-delta for typical FX vol levels.

  RR_proxy = IV(ATM + 1ATR call) − IV(ATM − 1ATR put)
  Positive → call skew (bullish)  |  Negative → put skew (bearish)

Score 0.0–1.0:
  1.0 → strong options-market bias in signal direction
  0.5 → neutral / data unavailable (fail-open)
  0.0 → options market biased opposite to signal direction

Redis key: fx_options_rr:{instrument}
TTL: 4 hours (daily options data; 4h prevents excessive yfinance calls
     while keeping the signal reasonably fresh within a trading session).

Requires: yfinance (pip install yfinance)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# ETF per instrument; inverted=True means ETF is LONG the quote currency
_ETF_MAP: dict[str, tuple[str, bool]] = {
    "EUR_USD": ("FXE", False),
    "GBP_USD": ("FXB", False),
    "USD_JPY": ("FXY", True),   # FXY = long JPY → invert for USD/JPY
    "AUD_USD": ("FXA", False),  # CurrencyShares Australian Dollar Trust
    "USD_CAD": ("FXC", True),   # CurrencyShares Canadian Dollar Trust (inverted: long CAD = short USD/CAD)
}

# Minimum days to expiry for option chain to be used
_MIN_DTE = 14
_MAX_DTE = 60

# Normalisation cap for risk reversal (in vol points).
# A 3-vol-point RR is historically very extreme; most sit within ±2.
_RR_SCALE = 3.0


def _compute_risk_reversal(etf_sym: str, inverted: bool) -> Optional[dict]:
    """Fetch ETF options chain and compute a risk-reversal proxy.

    Returns:
        {
            "raw_rr":     float,  # vol-point risk reversal (call IV - put IV)
            "raw_score":  float,  # normalised to [-1, 1]
            "call_iv":    float,
            "put_iv":     float,
            "expiry":     str,    # ISO date used
            "spot":       float,
            "atr_proxy":  float,  # strike distance used
        }
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance_not_installed", note="pip install yfinance to enable FX options signal")
        return None

    try:
        ticker  = yf.Ticker(etf_sym)
        hist    = ticker.history(period="20d", auto_adjust=True)

        if hist is None or len(hist) < 5:
            return None

        spot = float(hist["Close"].iloc[-1])

        # ── ATR proxy (14-day) for strike distance ────────────────────
        highs  = hist["High"].values
        lows   = hist["Low"].values
        closes = hist["Close"].values
        trs    = [max(h - l, abs(h - c), abs(l - c))
                  for h, l, c in zip(highs[1:], lows[1:], closes[:-1])]
        atr    = sum(trs[-14:]) / min(14, len(trs)) if trs else spot * 0.005
        atr    = max(atr, spot * 0.003)  # floor at 0.3% to avoid degenerate strikes

        # ── Select nearest expiry within DTE window ───────────────────
        try:
            exp_dates = ticker.options
        except Exception:
            return None

        if not exp_dates:
            return None

        today = datetime.now(timezone.utc).date()
        chosen_exp = None
        for exp_str in exp_dates:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                dte      = (exp_date - today).days
                if _MIN_DTE <= dte <= _MAX_DTE:
                    chosen_exp = exp_str
                    break
            except ValueError:
                continue

        if not chosen_exp:
            # Fall back to first available expiry beyond MIN_DTE
            for exp_str in exp_dates:
                try:
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    if (exp_date - today).days >= _MIN_DTE:
                        chosen_exp = exp_str
                        break
                except ValueError:
                    continue

        if not chosen_exp:
            return None

        # ── Fetch the option chain ────────────────────────────────────
        chain = ticker.option_chain(chosen_exp)
        calls = chain.calls
        puts  = chain.puts

        if calls is None or puts is None or calls.empty or puts.empty:
            return None

        # Target strikes: ATM ± 1 ATR
        call_target = spot + atr
        put_target  = spot - atr

        # Find closest call strike above ATM
        call_row = None
        call_dist = float("inf")
        for _, row in calls.iterrows():
            try:
                strike = float(row["strike"])
                iv     = float(row.get("impliedVolatility", float("nan")))
            except (ValueError, TypeError):
                continue
            if iv <= 0 or iv != iv:  # nan check
                continue
            dist = abs(strike - call_target)
            if dist < call_dist:
                call_dist = dist
                call_row  = (strike, iv)

        # Find closest put strike below ATM
        put_row  = None
        put_dist = float("inf")
        for _, row in puts.iterrows():
            try:
                strike = float(row["strike"])
                iv     = float(row.get("impliedVolatility", float("nan")))
            except (ValueError, TypeError):
                continue
            if iv <= 0 or iv != iv:
                continue
            dist = abs(strike - put_target)
            if dist < put_dist:
                put_dist = dist
                put_row  = (strike, iv)

        if call_row is None or put_row is None:
            return None

        call_iv = call_row[1]
        put_iv  = put_row[1]
        raw_rr  = call_iv - put_iv   # positive = call skew = bullish

        # Normalise to [-1, 1]
        raw_score = max(-1.0, min(1.0, raw_rr / _RR_SCALE))

        # Invert for USD_JPY (bullish FXY = bearish USD/JPY)
        if inverted:
            raw_score = -raw_score
            raw_rr    = -raw_rr

        return {
            "raw_rr":    round(raw_rr, 6),
            "raw_score": round(raw_score, 4),
            "call_iv":   round(call_iv, 6),
            "put_iv":    round(put_iv, 6),
            "expiry":    chosen_exp,
            "spot":      round(spot, 4),
            "atr_proxy": round(atr, 4),
        }

    except Exception as exc:
        logger.warning("fx_options_compute_failed", ticker=etf_sym, error=str(exc))
        return None


def fx_options_signal_score(raw_score: Optional[float], direction: str) -> float:
    """Map risk-reversal score to [0, 1] component weight."""
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
    """Compute FX options RR for one instrument and store in Redis."""
    mapping = _ETF_MAP.get(instrument)
    if not mapping:
        return None

    etf_sym, inverted = mapping
    result = _compute_risk_reversal(etf_sym, inverted)

    payload = {
        "result":     result,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    key = f"fx_options_rr:{instrument}"
    await redis_client.set(key, json.dumps(payload), ex=4 * 3_600)

    logger.debug(
        "fx_options_rr_stored",
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
        raw = await redis_client.get(f"fx_options_rr:{instrument}")
        if not raw:
            return 0.5, None

        payload = json.loads(raw)
        result  = payload.get("result")
        if not result:
            return 0.5, None

        score = fx_options_signal_score(result.get("raw_score"), direction)
        return score, result

    except Exception as exc:
        logger.warning("fx_options_cache_failed", instrument=instrument, error=str(exc))
        return 0.5, None
