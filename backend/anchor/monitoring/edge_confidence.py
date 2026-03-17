"""
Edge Confidence Monitor.

Detects whether the current macro environment is one where Anchor's
session-based structural edges (London Trend + LCR) are likely to hold.

Does NOT replace live performance monitoring — that catches degradation
AFTER it happens. This module catches leading indicators BEFORE trade
results deteriorate.

Three signals:

  1. SESSION CHARACTER — Is the London session behaving like a directional
     session (trending) or a choppy/whipsawing one? Structural edges
     require directional sessions. Persistent choppiness = edge under stress.

  2. PAIR CORRELATION — Do normally-correlated pairs still move together?
     EUR/USD and GBP/USD should correlate > 0.70 on a 10-day basis.
     Breakdown means a macro driver is splitting pairs that normally follow
     the same impulse — a sign the market is repricing something structural.

  3. MACRO STRESS — Is the volatility environment elevated and accelerating?
     Are multiple currencies in synchronized economic surprise? These
     precede session character breakdown.

Confidence levels:
  HIGH    → All three signals normal. Edge expected to hold.
  REDUCED → One signal flagged. Monitor closely, consider reducing size.
  LOW     → Two or more signals flagged. Recommend manual review before trading.

Output: SystemEvent(event_type='EDGE_CONFIDENCE_CHECK') + Telegram on change.

Usage (from Celery task):
    from anchor.monitoring.edge_confidence import assess_edge_confidence
    result = await assess_edge_confidence(db_session, redis_client)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
import structlog

from anchor.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# ── Active pairs ──────────────────────────────────────────────────────────────
_ACTIVE_PAIRS = settings.instruments  # derived from config — edit config.py to change

# Pairs that should be strongly correlated under normal conditions
_CORRELATION_PAIRS = [
    ("EUR_USD", "GBP_USD"),   # typically 0.80+
    ("EUR_USD", "AUD_USD"),   # typically 0.65+
    ("NZD_USD", "AUD_USD"),   # typically 0.85+
]
_CORRELATION_BASELINE_DAYS = 60   # long-window baseline
_CORRELATION_CURRENT_DAYS  = 10   # short-window to detect breakdown
_CORRELATION_DROP_THRESHOLD = 0.25  # flag if current drops this far below baseline

# London session hours (UTC) for session character analysis
_LONDON_HOURS = set(range(7, 13))   # 07:00–12:00 UTC
_SESSION_LOOKBACK_DAYS = 20         # analyse last N trading days
_SESSION_EFFICIENCY_FLOOR = 0.28    # below this = choppy session
_SESSION_FLAG_THRESHOLD   = 0.45    # if >45% of sessions are choppy → flag

# Macro stress thresholds
_VIX_ELEVATED      = 22.0   # VIX above this = elevated volatility
_VIX_ACCELERATING  = 3.0    # 5-day VIX change above this = accelerating
_SURPRISE_EXTREME  = 0.65   # |economic surprise score| above this = extreme
_SURPRISE_CURRENCY_MIN = 2  # need this many currencies in extreme to flag


# ── Result containers ─────────────────────────────────────────────────────────

@dataclass
class SignalResult:
    name:    str
    flagged: bool
    reason:  str
    detail:  dict = field(default_factory=dict)


@dataclass
class EdgeConfidenceResult:
    confidence:    str            # "HIGH" | "REDUCED" | "LOW"
    flags:         list[SignalResult]
    session:       SignalResult | None = None
    correlation:   SignalResult | None = None
    macro:         SignalResult | None = None
    previous:      str | None = None   # previous confidence level (for change detection)
    assessed_at:   datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def flag_count(self) -> int:
        return sum(1 for f in self.flags if f.flagged)

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence":  self.confidence,
            "flag_count":  self.flag_count,
            "assessed_at": self.assessed_at.isoformat(),
            "signals": {
                "session":     {"flagged": self.session.flagged,     "reason": self.session.reason,     "detail": self.session.detail}     if self.session     else None,
                "correlation": {"flagged": self.correlation.flagged, "reason": self.correlation.reason, "detail": self.correlation.detail} if self.correlation else None,
                "macro":       {"flagged": self.macro.flagged,       "reason": self.macro.reason,       "detail": self.macro.detail}       if self.macro       else None,
            },
            "previous_confidence": self.previous,
        }


# ── Signal 1: Session character ───────────────────────────────────────────────

async def _assess_session_character(db_session) -> SignalResult:
    """
    Measures London session directional efficiency over the last 20 trading days.

    Directional efficiency = |session_net_move| / sum(|hourly_ranges|)
    Range: [0, 1]. High value = trending session. Low = choppy/whipsawing.

    Flags if > 45% of recent sessions are choppy (efficiency < 0.28).
    """
    from sqlalchemy import select, and_
    from anchor.database.models import MarketData

    cutoff = datetime.now(timezone.utc) - timedelta(days=_SESSION_LOOKBACK_DAYS + 5)

    # Load H1 bars for all active pairs within London hours
    try:
        result = await db_session.execute(
            select(MarketData).where(
                and_(
                    MarketData.timeframe == "H1",
                    MarketData.instrument.in_(_ACTIVE_PAIRS),
                    MarketData.time >= cutoff,
                )
            ).order_by(MarketData.time)
        )
        bars = result.scalars().all()
    except Exception as exc:
        logger.warning("session_character_db_error", error=str(exc))
        return SignalResult("session_character", False, "DB read failed — skip", {})

    if not bars:
        return SignalResult("session_character", False, "No candle data available", {})

    df = pd.DataFrame([{
        "time":       b.time,
        "instrument": b.instrument,
        "open":       float(b.open),
        "high":       float(b.high),
        "low":        float(b.low),
        "close":      float(b.close),
    } for b in bars])

    df["time"] = pd.to_datetime(df["time"], utc=True)
    df["hour"] = df["time"].dt.hour
    df["date"] = df["time"].dt.date

    # Filter to London hours
    london = df[df["hour"].isin(_LONDON_HOURS)].copy()
    if len(london) < 20:
        return SignalResult("session_character", False, "Insufficient London bars", {})

    # Per-pair, per-day: compute session efficiency
    efficiencies: list[float] = []
    for (pair, date), group in london.groupby(["instrument", "date"]):
        group = group.sort_values("time")
        if len(group) < 3:
            continue
        session_open  = float(group["open"].iloc[0])
        session_close = float(group["close"].iloc[-1])
        net_move      = abs(session_close - session_open)
        total_range   = float((group["high"] - group["low"]).abs().sum())
        if total_range > 0:
            efficiencies.append(net_move / total_range)

    if len(efficiencies) < 10:
        return SignalResult("session_character", False, "Not enough session days", {})

    choppy_pct = sum(1 for e in efficiencies if e < _SESSION_EFFICIENCY_FLOOR) / len(efficiencies)
    avg_eff    = float(np.mean(efficiencies))

    flagged = choppy_pct > _SESSION_FLAG_THRESHOLD
    detail  = {
        "avg_efficiency":       round(avg_eff, 3),
        "choppy_session_pct":   round(choppy_pct * 100, 1),
        "sessions_analysed":    len(efficiencies),
        "efficiency_floor":     _SESSION_EFFICIENCY_FLOOR,
        "choppy_flag_threshold": _SESSION_FLAG_THRESHOLD * 100,
    }

    if flagged:
        reason = (
            f"{choppy_pct*100:.0f}% of London sessions are choppy "
            f"(avg efficiency {avg_eff:.2f}, threshold {_SESSION_EFFICIENCY_FLOOR})"
        )
    else:
        reason = f"Session character normal — avg efficiency {avg_eff:.2f}, {choppy_pct*100:.0f}% choppy"

    return SignalResult("session_character", flagged, reason, detail)


# ── Signal 2: Pair correlation breakdown ─────────────────────────────────────

async def _assess_correlation(db_session) -> SignalResult:
    """
    Compares 10-day vs 60-day rolling correlation for key pair combinations.
    Flags if any pair's current correlation drops > 0.25 below its baseline.
    """
    from sqlalchemy import select, and_
    from anchor.database.models import MarketData

    cutoff = datetime.now(timezone.utc) - timedelta(days=_CORRELATION_BASELINE_DAYS + 5)
    all_pairs = list({p for combo in _CORRELATION_PAIRS for p in combo})

    try:
        result = await db_session.execute(
            select(MarketData).where(
                and_(
                    MarketData.timeframe == "H1",
                    MarketData.instrument.in_(all_pairs),
                    MarketData.time >= cutoff,
                )
            ).order_by(MarketData.time)
        )
        bars = result.scalars().all()
    except Exception as exc:
        logger.warning("correlation_db_error", error=str(exc))
        return SignalResult("pair_correlation", False, "DB read failed — skip", {})

    if not bars:
        return SignalResult("pair_correlation", False, "No candle data available", {})

    df = pd.DataFrame([{
        "time":       b.time,
        "instrument": b.instrument,
        "close":      float(b.close),
    } for b in bars])
    df["time"] = pd.to_datetime(df["time"], utc=True)

    # Daily close prices
    daily = (
        df.groupby(["instrument", df["time"].dt.date])["close"]
        .last()
        .unstack(level=0)
    )
    daily_ret = daily.pct_change().dropna()

    breakdowns: list[dict] = []
    details:    dict[str, Any] = {}

    for p1, p2 in _CORRELATION_PAIRS:
        if p1 not in daily_ret.columns or p2 not in daily_ret.columns:
            continue

        series = daily_ret[[p1, p2]].dropna()
        if len(series) < _CORRELATION_CURRENT_DAYS + 5:
            continue

        baseline_corr = float(series.tail(_CORRELATION_BASELINE_DAYS).corr().iloc[0, 1])
        current_corr  = float(series.tail(_CORRELATION_CURRENT_DAYS).corr().iloc[0, 1])
        drop          = baseline_corr - current_corr

        pair_key = f"{p1}/{p2}"
        details[pair_key] = {
            "baseline_corr": round(baseline_corr, 3),
            "current_corr":  round(current_corr, 3),
            "drop":          round(drop, 3),
        }

        if drop > _CORRELATION_DROP_THRESHOLD:
            breakdowns.append({
                "pair":          pair_key,
                "baseline_corr": round(baseline_corr, 3),
                "current_corr":  round(current_corr, 3),
                "drop":          round(drop, 3),
            })

    flagged = len(breakdowns) > 0
    if flagged:
        bd_str = "; ".join(
            f"{b['pair']} {b['baseline_corr']:.2f}→{b['current_corr']:.2f}"
            for b in breakdowns
        )
        reason = f"Correlation breakdown detected: {bd_str}"
    else:
        reason = "Pair correlations stable"

    details["breakdowns"] = breakdowns
    return SignalResult("pair_correlation", flagged, reason, details)


# ── Signal 3: Macro stress ────────────────────────────────────────────────────

async def _assess_macro_stress(redis) -> SignalResult:
    """
    Checks VIX level + acceleration and economic surprise extremes from Redis.
    Both are already being maintained by existing scheduler jobs.
    """
    stress_reasons: list[str] = []
    detail: dict[str, Any]    = {}

    # ── VIX ──────────────────────────────────────────────────────────────────
    try:
        vix_raw = await redis.get("vix_latest")
        if vix_raw:
            vix_data = json.loads(vix_raw)
            vix      = float(vix_data.get("vix", 0))
            detail["vix"] = vix

            # VIX history for rate-of-change (stored in equity_curve or redis)
            # Fallback: use current level only if history unavailable
            vix_key = await redis.get("vix_history")
            vix_5d_change = None
            if vix_key:
                hist = json.loads(vix_key)
                if len(hist) >= 5:
                    vix_5d_change = vix - float(hist[-5])
                    detail["vix_5d_change"] = round(vix_5d_change, 2)

            if vix >= _VIX_ELEVATED:
                stress_reasons.append(f"VIX elevated at {vix:.1f} (threshold {_VIX_ELEVATED})")
            if vix_5d_change is not None and vix_5d_change >= _VIX_ACCELERATING:
                stress_reasons.append(f"VIX accelerating +{vix_5d_change:.1f} over 5 days")
        else:
            detail["vix"] = None
    except Exception as exc:
        logger.debug("vix_redis_error", error=str(exc))
        detail["vix_error"] = str(exc)

    # ── Economic surprise extremes ────────────────────────────────────────────
    _SURPRISE_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD"]
    extreme_currencies: list[str] = []
    surprise_scores: dict[str, float] = {}

    for currency in _SURPRISE_CURRENCIES:
        try:
            raw = await redis.get(f"econ_surprise:{currency}")
            if raw:
                score = float(json.loads(raw))
                surprise_scores[currency] = round(score, 3)
                # Score is [0,1] — centre is 0.5, extreme = |score - 0.5| > threshold
                if abs(score - 0.5) > (_SURPRISE_EXTREME - 0.5):
                    extreme_currencies.append(currency)
        except Exception:
            pass

    detail["econ_surprise"] = surprise_scores
    detail["extreme_currencies"] = extreme_currencies

    if len(extreme_currencies) >= _SURPRISE_CURRENCY_MIN:
        stress_reasons.append(
            f"Economic surprise extreme for {', '.join(extreme_currencies)} "
            f"({len(extreme_currencies)} currencies — synchronized macro repricing)"
        )

    # ── Cross-asset risk sentiment ─────────────────────────────────────────────
    try:
        cross_raw = await redis.get("cross_asset_risk")
        if cross_raw:
            cross = json.loads(cross_raw).get("result", {})
            sentiment = float(cross.get("risk_sentiment", 0))
            regime    = cross.get("regime", "NEUTRAL")
            detail["risk_sentiment"] = round(sentiment, 3)
            detail["risk_regime"]    = regime
            if abs(sentiment) > 0.7:
                stress_reasons.append(
                    f"Extreme cross-asset risk {regime} (sentiment {sentiment:+.2f}) — "
                    f"macro flow may override session mechanics"
                )
    except Exception as exc:
        logger.debug("cross_asset_redis_error", error=str(exc))

    flagged = len(stress_reasons) > 0
    reason  = "; ".join(stress_reasons) if stress_reasons else "Macro environment normal"
    return SignalResult("macro_stress", flagged, reason, detail)


# ── Aggregator ────────────────────────────────────────────────────────────────

async def assess_edge_confidence(
    db_session,
    redis,
    previous_confidence: str | None = None,
) -> EdgeConfidenceResult:
    """
    Run all three signals and aggregate into a single confidence level.

    Returns EdgeConfidenceResult with full breakdown.
    Caller is responsible for writing to system_events and sending alerts.
    """
    session_result = await _assess_session_character(db_session)
    corr_result    = await _assess_correlation(db_session)
    macro_result   = await _assess_macro_stress(redis)

    all_signals = [session_result, corr_result, macro_result]
    flag_count  = sum(1 for s in all_signals if s.flagged)

    if flag_count == 0:
        confidence = "HIGH"
    elif flag_count == 1:
        confidence = "REDUCED"
    else:
        confidence = "LOW"

    return EdgeConfidenceResult(
        confidence=confidence,
        flags=[s for s in all_signals if s.flagged],
        session=session_result,
        correlation=corr_result,
        macro=macro_result,
        previous=previous_confidence,
    )


# ── Alert message builder ─────────────────────────────────────────────────────

def build_alert_message(result: EdgeConfidenceResult) -> str:
    emoji = {"HIGH": "✅", "REDUCED": "⚠️", "LOW": "🚨"}.get(result.confidence, "❓")
    lines = [
        f"*Edge Confidence: {result.confidence}* {emoji}",
        "",
    ]

    if result.previous and result.previous != result.confidence:
        lines.append(f"_Changed from {result.previous} → {result.confidence}_")
        lines.append("")

    if not result.flags:
        lines.append("All signals normal. Edge expected to hold.")
    else:
        lines.append(f"{len(result.flags)} signal(s) flagged:")
        for f in result.flags:
            lines.append(f"• *{f.name}*: {f.reason}")

    lines += [
        "",
        "Action guide:",
        "  HIGH    → Trade normally",
        "  REDUCED → Monitor closely, consider 75% size",
        "  LOW     → Manual review before trading — macro may be overriding structure",
    ]

    return "\n".join(lines)
