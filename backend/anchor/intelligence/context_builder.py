"""
Assemble live context snapshot for LLM brief generation.

Pulls from:
  - Redis: macro caches (rates, COT, cross-asset, VIX, CME flow, options, surprises)
  - DB: signals, trades, economic calendar, equity curve, regime history
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_INSTRUMENTS = ["EUR_USD", "GBP_USD", "NZD_USD", "USD_CAD", "EUR_JPY", "AUD_USD"]
_CURRENCIES  = ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "NZD", "CHF"]


async def build_context(report_type: str, session, redis_client) -> dict[str, Any]:
    """
    Build context dict for LLM brief generation.

    report_type: PRESESSION | POSTSESSION | WEEKLY
    """
    now = datetime.now(timezone.utc)
    ctx: dict[str, Any] = {
        "report_type": report_type,
        "generated_at_utc": now.isoformat(),
    }

    ctx["macro"]           = await _get_macro_context(redis_client)
    ctx["account"]         = await _get_account_context(session)
    ctx["upcoming_events"] = await _get_upcoming_events(session, hours=168 if report_type == "WEEKLY" else 24)
    ctx["recent_signals"]  = await _get_recent_signals(session, hours=12 if report_type == "PRESESSION" else 8)
    ctx["recent_trades"]   = await _get_recent_trades(session, days=7 if report_type == "WEEKLY" else 2)
    ctx["performance_30d"] = await _get_rolling_performance(session)
    ctx["regime"]          = await _get_regime_context(session)
    return ctx


# ── private helpers ──────────────────────────────────────────────────────────

async def _get_macro_context(redis_client) -> dict:
    macro: dict[str, Any] = {}

    async def _get(key: str) -> Any:
        try:
            raw = await redis_client.get(key)
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.warning("redis_get_error", key=key, error=str(exc))
            return None

    for key, label in [
        ("fred_rate_diff",   "rate_differentials"),
        ("cot_data",         "cot_positioning"),
        ("cross_asset_risk", "cross_asset"),
        ("vix_data",         "vix"),
    ]:
        val = await _get(key)
        if val is not None:
            macro[label] = val

    surprises = {}
    for ccy in _CURRENCIES:
        val = await _get(f"econ_surprise:{ccy}")
        if val is not None:
            surprises[ccy] = val
    if surprises:
        macro["economic_surprise"] = surprises

    cme_flows, options_rr = {}, {}
    for pair in _INSTRUMENTS:
        flow = await _get(f"cme_flow:{pair}")
        if flow is not None:
            cme_flows[pair] = flow
        rr = await _get(f"fx_options_rr:{pair}")
        if rr is not None:
            options_rr[pair] = rr
    if cme_flows:
        macro["cme_flow"] = cme_flows
    if options_rr:
        macro["fx_options_rr"] = options_rr

    return macro


async def _get_account_context(session) -> dict:
    from anchor.database.models import EquityCurvePoint
    from sqlalchemy import select, desc

    try:
        result = await session.execute(
            select(EquityCurvePoint).order_by(desc(EquityCurvePoint.time)).limit(1)
        )
        pt = result.scalar_one_or_none()
        if pt:
            return {
                "balance":       float(pt.account_balance),
                "equity":        float(pt.account_equity),
                "drawdown_pct":  float(pt.drawdown_pct or 0),
                "unrealized_pl": float(pt.unrealized_pl or 0),
                "as_of_utc":     pt.time.isoformat(),
            }
    except Exception as exc:
        logger.warning("account_context_error", error=str(exc))
    return {}


async def _get_upcoming_events(session, hours: int = 24) -> list[dict]:
    from anchor.database.models import EconomicEvent
    from sqlalchemy import select, and_

    now    = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours)
    try:
        result = await session.execute(
            select(EconomicEvent)
            .where(and_(
                EconomicEvent.event_time >= now,
                EconomicEvent.event_time <= cutoff,
                EconomicEvent.impact == "HIGH",
            ))
            .order_by(EconomicEvent.event_time)
        )
        return [
            {
                "time_utc":  e.event_time.isoformat(),
                "currency":  e.currency,
                "event":     e.event_name,
                "forecast":  e.forecast,
                "previous":  e.previous,
                "actual":    e.actual,
            }
            for e in result.scalars().all()
        ]
    except Exception as exc:
        logger.warning("upcoming_events_error", error=str(exc))
    return []


async def _get_recent_signals(session, hours: int = 12) -> dict:
    from anchor.database.models import Signal
    from sqlalchemy import select, desc

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    try:
        result = await session.execute(
            select(Signal)
            .where(Signal.created_at >= cutoff)
            .order_by(desc(Signal.created_at))
            .limit(150)
        )
        signals = result.scalars().all()

        fired, suppression_counts = [], {}
        for s in signals:
            if not s.suppressed and s.direction not in (None, "NONE"):
                fired.append({
                    "instrument": s.instrument,
                    "direction":  s.direction,
                    "score":      float(s.confluence_score),
                    "regime":     s.regime_state,
                    "session":    s.session,
                    "at_utc":     s.created_at.isoformat(),
                })
            elif s.suppressed and s.suppression_reason:
                suppression_counts[s.suppression_reason] = suppression_counts.get(s.suppression_reason, 0) + 1

        return {
            "fired":               fired[:25],
            "total_evaluated":     len(signals),
            "total_fired":         len(fired),
            "suppression_summary": suppression_counts,
        }
    except Exception as exc:
        logger.warning("recent_signals_error", error=str(exc))
    return {}


async def _get_recent_trades(session, days: int = 2) -> list[dict]:
    from anchor.database.models import Trade
    from sqlalchemy import select, desc

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        result = await session.execute(
            select(Trade)
            .where(Trade.closed_at >= cutoff)
            .order_by(desc(Trade.closed_at))
            .limit(100)
        )
        return [
            {
                "instrument":  t.instrument,
                "direction":   t.direction,
                "net_pl":      float(t.net_pl),
                "close_reason": t.close_reason,
                "regime":      t.regime_at_entry,
                "session":     t.session_at_entry,
                "opened_utc":  t.opened_at.isoformat() if t.opened_at else None,
                "closed_utc":  t.closed_at.isoformat() if t.closed_at else None,
                "duration_min": t.duration_minutes,
            }
            for t in result.scalars().all()
        ]
    except Exception as exc:
        logger.warning("recent_trades_error", error=str(exc))
    return []


async def _get_rolling_performance(session) -> dict:
    from anchor.database.models import Trade
    from sqlalchemy import select

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    try:
        result = await session.execute(select(Trade).where(Trade.closed_at >= cutoff))
        trades = result.scalars().all()
        if not trades:
            return {"trade_count": 0}

        pls      = [float(t.net_pl) for t in trades]
        wins     = [p for p in pls if p > 0]
        losses   = [p for p in pls if p <= 0]
        gw       = sum(wins) if wins else 0.0
        gl       = abs(sum(losses)) if losses else 0.0

        by_pair: dict[str, dict] = {}
        for t in trades:
            s = by_pair.setdefault(t.instrument, {"count": 0, "pl": 0.0, "wins": 0})
            s["count"] += 1
            s["pl"] += float(t.net_pl)
            if float(t.net_pl) > 0:
                s["wins"] += 1
        for s in by_pair.values():
            s["win_rate"] = round(s["wins"] / s["count"], 3)
            s["pl"] = round(s["pl"], 2)

        return {
            "trade_count":   len(trades),
            "win_rate":      round(len(wins) / len(trades), 3),
            "profit_factor": round(gw / gl, 2) if gl > 0 else None,
            "net_pl":        round(sum(pls), 2),
            "per_pair":      by_pair,
        }
    except Exception as exc:
        logger.warning("rolling_performance_error", error=str(exc))
    return {}


async def _get_regime_context(session) -> dict:
    from anchor.database.models import RegimeHistory
    from sqlalchemy import select, desc

    regimes: dict[str, str] = {}
    for pair in _INSTRUMENTS:
        try:
            result = await session.execute(
                select(RegimeHistory)
                .where(RegimeHistory.instrument == pair)
                .order_by(desc(RegimeHistory.time))
                .limit(1)
            )
            rh = result.scalar_one_or_none()
            if rh:
                regimes[pair] = rh.regime
        except Exception as exc:
            logger.warning("regime_context_error", pair=pair, error=str(exc))
    return regimes
