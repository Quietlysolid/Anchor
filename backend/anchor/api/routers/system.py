from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.build_info import DEPLOYED_AT, DEPLOYED_SHA
from anchor.config import get_settings
from anchor.database.engine import get_db
from anchor.api.schemas import RolloutConfigResponse, LCRPairStatusesResponse
from anchor.utils.time_utils import utcnow

router = APIRouter()
settings = get_settings()

# Set by main.py lifespan after stream starts
_stream_connected: bool = False
_account_balance: float = 0.0
_account_equity: float = 0.0
_last_reconciliation: str | None = None


def set_stream_status(connected: bool) -> None:
    global _stream_connected
    _stream_connected = connected


def set_account_info(balance: float, equity: float, reconciled_at: str | None = None) -> None:
    global _account_balance, _account_equity, _last_reconciliation
    _account_balance = balance
    _account_equity = equity
    if reconciled_at:
        _last_reconciliation = reconciled_at


@router.get("/system/health")
async def health_check(session: AsyncSession = Depends(get_db)):
    """System health endpoint. Used by Docker healthcheck and monitoring."""
    from sqlalchemy import text, select, func
    from anchor.database.models import Position, EquityCurvePoint

    now_utc = utcnow()
    now_et = now_utc.astimezone(ZoneInfo("America/New_York"))
    et_midnight = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_midnight = et_midnight.astimezone(ZoneInfo("UTC"))

    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    try:
        result = await session.execute(
            select(func.count()).select_from(Position).where(Position.status == "OPEN")
        )
        open_positions = result.scalar() or 0
    except Exception:
        open_positions = 0

    try:
        result = await session.execute(
            select(EquityCurvePoint.account_equity)
            .where(EquityCurvePoint.time < utc_midnight)
            .order_by(EquityCurvePoint.time.desc())
            .limit(1)
        )
        start_of_day_equity = result.scalar_one_or_none()
    except Exception:
        start_of_day_equity = None

    today_pl = None
    if start_of_day_equity is not None:
        today_pl = _account_equity - float(start_of_day_equity)

    return {
        "status":              "ok" if db_ok else "degraded",
        "db":                  "ok" if db_ok else "error",
        "timestamp":           now_utc.isoformat(),
        "deployed_sha":        DEPLOYED_SHA,
        "deployed_at":         DEPLOYED_AT,
        "stream_connected":    _stream_connected,
        "account_balance":     _account_balance,
        "account_equity":      _account_equity,
        "today_pl":            today_pl,
        "open_positions":      open_positions,
        "last_reconciliation": _last_reconciliation,
    }


@router.get("/system/config", response_model=RolloutConfigResponse)
async def get_system_config():
    """Return the active trading rollout configuration."""
    return {
        "instruments": settings.instruments,
        "trend": {
            "enabled": settings.enable_trend_engine,
            "paper_only": settings.trend_paper_only,
            "risk_pct": settings.trend_risk_pct,
            "instruments": settings.trend_instruments,
        },
        "mean_reversion": {
            "enabled": settings.enable_mr_engine,
            "paper_only": settings.mr_paper_only,
            "risk_pct": settings.mr_risk_pct,
        },
        "lcr": {
            "enabled": settings.enable_lcr_engine,
            "paper_only": settings.lcr_paper_only,
            "risk_pct": settings.lcr_risk_pct,
        },
        "fix": {
            "enabled": settings.enable_fix_engine,
            "paper_only": settings.fix_paper_only,
            "risk_pct": settings.fix_risk_pct,
            "instruments": settings.fix_instruments,
        },
        "nfp": {
            "enabled": settings.enable_nfp_engine,
            "paper_only": settings.nfp_paper_only,
            "risk_pct": settings.nfp_risk_pct,
            "instruments": settings.nfp_instruments,
        },
        "m15": {
            "enabled": settings.enable_m15_engine,
            "paper_only": settings.m15_paper_only,
            "risk_pct": settings.m15_risk_pct,
        },
        "expected_pilot": {
            "instruments": settings.expected_pilot_instruments,
            "trend": {
                "enabled": settings.expected_pilot_trend_enabled,
                "paper_only": settings.expected_pilot_trend_paper_only,
                "risk_pct": settings.expected_pilot_trend_risk_pct,
            },
            "mean_reversion": {
                "enabled": settings.expected_pilot_mr_enabled,
                "paper_only": settings.expected_pilot_mr_paper_only,
                "risk_pct": settings.expected_pilot_mr_risk_pct,
            },
            "lcr": {
                "enabled": settings.expected_pilot_lcr_enabled,
                "paper_only": settings.expected_pilot_lcr_paper_only,
                "risk_pct": settings.expected_pilot_lcr_risk_pct,
            },
            "m15": {
                "enabled": settings.expected_pilot_m15_enabled,
                "paper_only": settings.expected_pilot_m15_paper_only,
                "risk_pct": settings.expected_pilot_m15_risk_pct,
            },
        },
        "max_risk_per_trade_fallback": settings.max_risk_per_trade,
        "min_confluence_score": settings.min_confluence_score,
        "min_ml_confidence": settings.min_ml_confidence,
    }


@router.get("/system/lcr-pair-status", response_model=LCRPairStatusesResponse)
async def get_lcr_pair_status(session: AsyncSession = Depends(get_db)):
    """
    Evaluate per-pair LCR status using precommitted rules.
    Returns status (active/watchlist/disabled), machine-readable reasons, and metrics for each pair.
    """
    from datetime import datetime, timezone
    from anchor.signals.lcr_pair_status import load_lcr_pair_statuses, LCR_LIVE_DATE

    now = datetime.now(timezone.utc)
    days_since_live = max(0, (now.date() - LCR_LIVE_DATE).days)

    pair_statuses = await load_lcr_pair_statuses(session)

    results = [
        {
            "instrument": r.instrument,
            "status":     r.status.value,
            "reasons":    r.reasons,
            "metrics":    r.metrics,
        }
        for r in pair_statuses.values()
    ]

    return {
        "pairs":           results,
        "evaluated_at":    now.isoformat(),
        "days_since_live": days_since_live,
    }


@router.get("/system/lcr-pair-status/history")
async def get_lcr_pair_status_history(
    days: int = 30,
    session: AsyncSession = Depends(get_db),
):
    """
    Return daily LCR pair-status snapshots for the last N days.
    Each snapshot is one LCR_PAIR_STATUS_SNAPSHOT system event written at 21:05 UTC.
    """
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import text

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (await session.execute(
        text("""
            SELECT event_at, metadata
            FROM system_events
            WHERE event_type = 'LCR_PAIR_STATUS_SNAPSHOT'
              AND event_at >= :cutoff
            ORDER BY event_at DESC
            LIMIT :limit
        """),
        {"cutoff": cutoff, "limit": days},
    )).fetchall()

    snapshots = []
    for r in rows:
        meta = dict(r[1] or {})
        snapshots.append({
            "date":         r[0].strftime("%Y-%m-%d"),
            "evaluated_at": r[0].isoformat(),
            "pairs":        meta.get("pairs", []),
            "summary":      meta.get("summary", {}),
        })

    return {"snapshots": snapshots}


@router.get("/system/edge-confidence")
async def get_edge_confidence(session: AsyncSession = Depends(get_db)):
    """Latest edge confidence assessment."""
    from sqlalchemy import select, desc
    from anchor.database.models import SystemEvent

    result = await session.execute(
        select(SystemEvent)
        .where(SystemEvent.event_type == "EDGE_CONFIDENCE_CHECK")
        .order_by(desc(SystemEvent.event_at))
        .limit(1)
    )
    event = result.scalars().first()
    if not event:
        return {"confidence": None, "assessed_at": None, "signals": None, "flag_count": 0, "message": None}

    meta = dict(event.metadata_ or {})
    return {
        "confidence":  meta.get("confidence"),
        "assessed_at": event.event_at.isoformat(),
        "flag_count":  meta.get("flag_count", 0),
        "message":     event.message,
        "signals":     meta.get("signals"),
        "previous_confidence": meta.get("previous_confidence"),
    }


@router.get("/system/fix-paper-summary")
async def get_fix_paper_summary(session: AsyncSession = Depends(get_db)):
    """Aggregate scored LDN_FIX paper signals into a sleeve summary."""
    overall_row = (await session.execute(text("""
        SELECT
            COUNT(*) AS n_scored,
            AVG(CAST(signal_metadata->>'realized_ret_pips' AS double precision)) AS mean_ret_pips,
            AVG(CASE WHEN (signal_metadata->>'continuation_success')::boolean THEN 1.0 ELSE 0.0 END) AS wr,
            AVG(CAST(signal_metadata->>'entry_slippage_pips' AS double precision)) AS avg_entry_slippage_pips,
            AVG(CAST(signal_metadata->>'max_favorable_pips' AS double precision)) AS avg_mfe_pips,
            AVG(CAST(signal_metadata->>'max_adverse_pips' AS double precision)) AS avg_mae_pips
        FROM signals
        WHERE session = 'LDN_FIX'
          AND suppressed = false
          AND signal_metadata ? 'paper_result_version'
    """))).fetchone()

    pair_rows = (await session.execute(text("""
        SELECT
            instrument,
            COUNT(*) AS n_scored,
            AVG(CAST(signal_metadata->>'realized_ret_pips' AS double precision)) AS mean_ret_pips,
            AVG(CASE WHEN (signal_metadata->>'continuation_success')::boolean THEN 1.0 ELSE 0.0 END) AS wr,
            AVG(CAST(signal_metadata->>'entry_slippage_pips' AS double precision)) AS avg_entry_slippage_pips,
            AVG(CAST(signal_metadata->>'pre_move_pips' AS double precision)) AS avg_pre_move_pips
        FROM signals
        WHERE session = 'LDN_FIX'
          AND suppressed = false
          AND signal_metadata ? 'paper_result_version'
        GROUP BY instrument
        ORDER BY AVG(CAST(signal_metadata->>'realized_ret_pips' AS double precision)) DESC NULLS LAST,
                 instrument
    """))).fetchall()

    recent_events = (await session.execute(text("""
        SELECT event_at, message, metadata
        FROM system_events
        WHERE event_type = 'FIX_PAPER_SCORE'
        ORDER BY event_at DESC
        LIMIT 5
    """))).fetchall()

    overall = {
        "n_scored": int(overall_row[0] or 0) if overall_row else 0,
        "mean_ret_pips": round(float(overall_row[1] or 0.0), 2) if overall_row else 0.0,
        "win_rate": round(float(overall_row[2] or 0.0) * 100.0, 1) if overall_row else 0.0,
        "avg_entry_slippage_pips": round(float(overall_row[3] or 0.0), 2) if overall_row else 0.0,
        "avg_mfe_pips": round(float(overall_row[4] or 0.0), 2) if overall_row else 0.0,
        "avg_mae_pips": round(float(overall_row[5] or 0.0), 2) if overall_row else 0.0,
    }
    pairs = [
        {
            "instrument": r[0],
            "n_scored": int(r[1] or 0),
            "mean_ret_pips": round(float(r[2] or 0.0), 2),
            "win_rate": round(float(r[3] or 0.0) * 100.0, 1),
            "avg_entry_slippage_pips": round(float(r[4] or 0.0), 2),
            "avg_pre_move_pips": round(float(r[5] or 0.0), 2),
        }
        for r in pair_rows
    ]
    events = [
        {
            "event_at": r[0].isoformat(),
            "message": r[1],
            "metadata": dict(r[2] or {}),
        }
        for r in recent_events
    ]

    return {"overall": overall, "pairs": pairs, "recent_scoring_events": events}


@router.get("/system/nfp-paper-summary")
async def get_nfp_paper_summary(session: AsyncSession = Depends(get_db)):
    overall_row = (await session.execute(text("""
        SELECT
            COUNT(*) AS n_scored,
            AVG(CAST(signal_metadata->>'realized_ret_pips' AS double precision)) AS mean_ret_pips,
            AVG(CASE WHEN (signal_metadata->>'continuation_success')::boolean THEN 1.0 ELSE 0.0 END) AS wr,
            AVG(CAST(signal_metadata->>'max_favorable_pips' AS double precision)) AS avg_mfe_pips,
            AVG(CAST(signal_metadata->>'max_adverse_pips' AS double precision)) AS avg_mae_pips
        FROM signals
        WHERE session = 'NFP_DRIFT'
          AND suppressed = false
          AND signal_metadata ? 'paper_result_version'
    """))).fetchone()

    pair_rows = (await session.execute(text("""
        SELECT
            instrument,
            COUNT(*) AS n_scored,
            AVG(CAST(signal_metadata->>'realized_ret_pips' AS double precision)) AS mean_ret_pips,
            AVG(CASE WHEN (signal_metadata->>'continuation_success')::boolean THEN 1.0 ELSE 0.0 END) AS wr
        FROM signals
        WHERE session = 'NFP_DRIFT'
          AND suppressed = false
          AND signal_metadata ? 'paper_result_version'
        GROUP BY instrument
        ORDER BY AVG(CAST(signal_metadata->>'realized_ret_pips' AS double precision)) DESC NULLS LAST,
                 instrument
    """))).fetchall()

    recent_events = (await session.execute(text("""
        SELECT event_at, message, metadata
        FROM system_events
        WHERE event_type = 'NFP_PAPER_SCORE'
        ORDER BY event_at DESC
        LIMIT 5
    """))).fetchall()

    overall = {
        "n_scored": int(overall_row[0] or 0) if overall_row else 0,
        "mean_ret_pips": round(float(overall_row[1] or 0.0), 2) if overall_row else 0.0,
        "win_rate": round(float(overall_row[2] or 0.0) * 100.0, 1) if overall_row else 0.0,
        "avg_mfe_pips": round(float(overall_row[3] or 0.0), 2) if overall_row else 0.0,
        "avg_mae_pips": round(float(overall_row[4] or 0.0), 2) if overall_row else 0.0,
    }
    pairs = [
        {
            "instrument": r[0],
            "n_scored": int(r[1] or 0),
            "mean_ret_pips": round(float(r[2] or 0.0), 2),
            "win_rate": round(float(r[3] or 0.0) * 100.0, 1),
        }
        for r in pair_rows
    ]
    events = [
        {"event_at": r[0].isoformat(), "message": r[1], "metadata": dict(r[2] or {})}
        for r in recent_events
    ]
    return {"overall": overall, "pairs": pairs, "recent_scoring_events": events}


@router.get("/system/events")
async def get_system_events(
    severity: str | None = None,
    limit:    int = 100,
    session:  AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, desc
    from anchor.database.models import SystemEvent

    q = select(SystemEvent).order_by(desc(SystemEvent.event_at)).limit(limit)
    if severity:
        severities = [s.strip().upper() for s in severity.split(",")]
        q = q.where(SystemEvent.severity.in_(severities))

    result = await session.execute(q)
    events = result.scalars().all()
    return [
        {
            "id":         e.id,
            "event_at":   e.event_at.isoformat(),
            "event_type": e.event_type,
            "severity":   e.severity,
            "component":  e.component,
            "message":    e.message,
            "metadata":   dict(e.metadata_ or {}),
        }
        for e in events
    ]
