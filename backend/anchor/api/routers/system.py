from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.build_info import DEPLOYED_AT, DEPLOYED_SHA
from anchor.config import get_settings
from anchor.database.engine import get_db
from anchor.api.schemas import (
    LCRPairStatusesResponse,
    OperatorStateResponse,
    RolloutConfigResponse,
)
from anchor.database.models import EconomicEvent, Order, Position, RegimeHistory, Signal, SystemEvent, Trade
from anchor.signals.session_filter import ASIAN_SESSION_END, ASIAN_SESSION_START
from anchor.utils.time_utils import utcnow

router = APIRouter()
settings = get_settings()

# Set by main.py lifespan after stream starts
_stream_connected: bool = False
_account_balance: float = 0.0
_account_equity: float = 0.0
_last_reconciliation: str | None = None
_WORKING_ORDER_STATES = ("PENDING", "SUBMITTED", "ACKNOWLEDGED", "PARTIAL")


def set_stream_status(connected: bool) -> None:
    global _stream_connected
    _stream_connected = connected


def set_account_info(balance: float, equity: float, reconciled_at: str | None = None) -> None:
    global _account_balance, _account_equity, _last_reconciliation
    _account_balance = balance
    _account_equity = equity
    if reconciled_at:
        _last_reconciliation = reconciled_at


def _window_payload(engine: str, label: str, starts_at: datetime, ends_at: datetime) -> dict:
    return {
        "engine": engine,
        "label": label,
        "starts_at": starts_at,
        "ends_at": ends_at,
    }


def _candidate_windows(now_utc: datetime) -> list[dict]:
    weekday = now_utc.weekday()
    candidates: list[dict] = []

    def day_anchor(offset_days: int = 0) -> datetime:
        return datetime(
            now_utc.year,
            now_utc.month,
            now_utc.day,
            tzinfo=timezone.utc,
        ) + timedelta(days=offset_days)

    if settings.enable_trend_engine or settings.enable_mr_engine or settings.enable_m15_engine:
        for offset in range(-1, 8):
            base = day_anchor(offset)
            wd = base.weekday()
            if wd >= 5:
                continue
            london_start = base.replace(hour=7, minute=15)
            london_end = base.replace(hour=12, minute=0)
            candidates.append(_window_payload("trend", "London core", london_start, london_end))

            has_jpy = any("JPY" in instrument for instrument in settings.instruments)
            if has_jpy:
                asian_start_hour = 2 if wd == 0 else ASIAN_SESSION_START
                asian_start = base.replace(hour=asian_start_hour, minute=0)
                asian_end = base.replace(hour=ASIAN_SESSION_END, minute=0)
                if asian_start < asian_end:
                    candidates.append(_window_payload("trend", "Tokyo JPY watch", asian_start, asian_end))

    if settings.enable_lcr_engine:
        for offset in range(-1, 8):
            base = day_anchor(offset)
            wd = base.weekday()
            if wd >= 5:
                continue
            lcr_start = base.replace(hour=17, minute=0)
            lcr_end = base.replace(hour=18 if wd == 4 else 20, minute=0)
            candidates.append(_window_payload("lcr", "London close", lcr_start, lcr_end))

    if settings.enable_fix_engine:
        from anchor.backtesting.fix_calendar import london_fix_time_utc
        import pandas as pd

        for offset in range(-1, 8):
            base = day_anchor(offset)
            if base.weekday() >= 5:
                continue
            fix_time = london_fix_time_utc(pd.Timestamp(base))
            candidates.append(_window_payload("fix", "London fix", fix_time.to_pydatetime(), (fix_time + pd.Timedelta(hours=1)).to_pydatetime()))

    return sorted(candidates, key=lambda item: item["starts_at"])


def _active_and_next_window(now_utc: datetime) -> tuple[dict | None, dict | None]:
    candidates = _candidate_windows(now_utc)
    active = next((window for window in candidates if window["starts_at"] <= now_utc < window["ends_at"]), None)
    next_window = next((window for window in candidates if window["starts_at"] > now_utc), None)
    return active, next_window


def _operator_state_code(
    *,
    db_ok: bool,
    stream_connected: bool,
    open_positions_count: int,
    working_orders_count: int,
    active_window: dict | None,
) -> str:
    if not db_ok or not stream_connected:
        return "DEGRADED"
    if open_positions_count > 0:
        return "MANAGING_POSITIONS"
    if working_orders_count > 0:
        return "WAITING_ON_ORDERS"
    if active_window:
        return "SCANNING"
    return "OFF_WINDOW"


def _strategy_states(active_window: dict | None) -> list[dict]:
    def readiness_for(engine: str) -> tuple[str, str]:
        if engine == "lcr":
            return "live_ready", "live-ready"
        if engine == "trend":
            return "paper_trial", "paper trial"
        if engine == "fix":
            return "paper_validated", "validated, paper-only"
        if engine == "nfp":
            return "paper_validated", "validated, paper-only"
        if engine == "mean_reversion":
            return "no_go", "no-go"
        if engine == "m15":
            return "research_only", "research only"
        return "unknown", "unknown"

    configured = [
        ("trend", "TREND", settings.enable_trend_engine, settings.trend_paper_only),
        ("lcr", "London close", settings.enable_lcr_engine, settings.lcr_paper_only),
        ("fix", "London fix", settings.enable_fix_engine, settings.fix_paper_only),
        ("nfp", "NFP", settings.enable_nfp_engine, settings.nfp_paper_only),
        ("mean_reversion", "Mean reversion", settings.enable_mr_engine, settings.mr_paper_only),
        ("m15", "M15", settings.enable_m15_engine, settings.m15_paper_only),
    ]

    states = []
    active_engine = active_window["engine"] if active_window else None
    for key, label, enabled, paper_only in configured:
        readiness, readiness_label = readiness_for(key)
        if not enabled:
            status = "off"
        elif active_engine == key:
            status = "running"
        else:
            status = "enabled"
        states.append({
            "engine": key,
            "label": label,
            "status": status,
            "paper_only": paper_only,
            "readiness": readiness,
            "readiness_label": readiness_label,
        })
    return states


def _signal_activity(signal: Signal) -> dict:
    if signal.suppressed:
        return {
            "id": f"signal-{signal.id}",
            "occurred_at": signal.created_at.isoformat(),
            "kind": "signal_blocked",
            "tone": "warn",
            "badge": "Skipped",
            "instrument": signal.instrument,
            "reason_code": signal.suppression_reason,
            "title": f"Signal checked on {signal.instrument}",
            "detail": signal.suppression_reason,
        }
    return {
        "id": f"signal-{signal.id}",
        "occurred_at": signal.created_at.isoformat(),
        "kind": "signal_passed",
        "tone": "good",
        "badge": "Setup",
        "instrument": signal.instrument,
        "reason_code": None,
        "title": f"Setup passed on {signal.instrument}",
        "detail": f"{signal.direction} with confluence {float(signal.confluence_score):.2f}",
    }


def _order_activity(order: Order) -> dict:
    tone = "good" if order.state == "FILLED" else "bad" if order.state == "REJECTED" else "info"
    return {
        "id": f"order-{order.id}",
        "occurred_at": order.created_at.isoformat(),
        "kind": "order",
        "tone": tone,
        "badge": order.state,
        "instrument": order.instrument,
        "reason_code": order.state,
        "title": f"Order {order.state.lower()} on {order.instrument}",
        "detail": f"{order.order_type} {order.direction} for {float(order.requested_units):.0f} units",
    }


def _trade_activity(trade: Trade) -> dict:
    won = float(trade.net_pl) > 0
    return {
        "id": f"trade-{trade.id}",
        "occurred_at": trade.closed_at.isoformat(),
        "kind": "trade_closed",
        "tone": "good" if won else "bad",
        "badge": "Closed",
        "instrument": trade.instrument,
        "reason_code": trade.close_reason,
        "title": f"Trade closed on {trade.instrument}",
        "detail": f"{trade.direction} finished at {float(trade.net_pl):.2f} USD",
    }


def _system_event_activity(event: SystemEvent) -> dict:
    sev = event.severity.upper()
    return {
        "id": f"event-{event.id}",
        "occurred_at": event.event_at.isoformat(),
        "kind": "system_event",
        "tone": "bad" if sev == "ERROR" else "warn" if sev == "WARNING" else "info",
        "badge": sev,
        "instrument": None,
        "reason_code": event.event_type,
        "title": event.message,
        "detail": event.event_type,
    }


def _is_interesting_system_event(event: SystemEvent) -> bool:
    event_type = event.event_type.upper()
    severity = event.severity.upper()
    if event_type == "HEARTBEAT":
        return False
    if severity in {"ERROR", "CRITICAL", "WARNING", "WARN"}:
        return True
    return event_type in {"RECONCILIATION", "LIVE_PERF_CHECK", "EDGE_CONFIDENCE_CHECK"}


def _collapse_activity(rows: list[dict]) -> list[dict]:
    collapsed: list[dict] = []
    i = 0
    while i < len(rows):
        row = rows[i]
        if row["kind"] != "signal_blocked":
            collapsed.append(row)
            i += 1
            continue

        reason_code = row.get("reason_code")
        occurred_at = row["occurred_at"][:16]
        bucket = [row]
        j = i + 1
        while j < len(rows):
            candidate = rows[j]
            if (
                candidate["kind"] == "signal_blocked"
                and candidate.get("reason_code") == reason_code
                and candidate["occurred_at"][:16] == occurred_at
            ):
                bucket.append(candidate)
                j += 1
                continue
            break

        if len(bucket) >= 2:
            instruments = [item["instrument"] for item in bucket if item.get("instrument")]
            collapsed.append({
                "id": f"batch-{bucket[0]['id']}",
                "occurred_at": bucket[0]["occurred_at"],
                "kind": "signal_blocked_batch",
                "tone": "warn",
                "badge": f"Skipped x{len(bucket)}",
                "instrument": None,
                "reason_code": reason_code,
                "title": f"{len(bucket)} setups got blocked",
                "detail": ", ".join(instruments),
            })
        else:
            collapsed.extend(bucket)

        i = j

    return collapsed


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
        "account_mode":        "paper" if settings.oanda_environment == "practice" else "live",
        "account_environment": settings.oanda_environment,
        "stream_connected":    _stream_connected,
        "account_balance":     _account_balance,
        "account_equity":      _account_equity,
        "today_pl":            today_pl,
        "open_positions":      open_positions,
        "last_reconciliation": _last_reconciliation,
    }


@router.get("/system/operator-state", response_model=OperatorStateResponse)
async def get_operator_state(session: AsyncSession = Depends(get_db)):
    now_utc = utcnow()

    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    open_positions_count = (
        await session.execute(
            select(func.count()).select_from(Position).where(Position.status == "OPEN")
        )
    ).scalar() or 0

    working_orders_count = (
        await session.execute(
            select(func.count()).select_from(Order).where(Order.state.in_(_WORKING_ORDER_STATES))
        )
    ).scalar() or 0

    latest_blocker = (
        await session.execute(
            select(Signal)
            .where(Signal.suppressed.is_(True))
            .order_by(desc(Signal.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()

    active_window, next_window = _active_and_next_window(now_utc)
    operator_state = _operator_state_code(
        db_ok=db_ok,
        stream_connected=_stream_connected,
        open_positions_count=int(open_positions_count),
        working_orders_count=int(working_orders_count),
        active_window=active_window,
    )

    active_engines = [
        engine
        for engine, enabled in (
            ("trend", settings.enable_trend_engine),
            ("mean_reversion", settings.enable_mr_engine),
            ("lcr", settings.enable_lcr_engine),
            ("fix", settings.enable_fix_engine),
            ("nfp", settings.enable_nfp_engine),
            ("m15", settings.enable_m15_engine),
        )
        if enabled
    ]

    blocker_code = latest_blocker.suppression_reason if latest_blocker else None
    blocker_reason = blocker_code.split(":", 1)[0] if blocker_code else None

    return {
        "as_of": now_utc,
        "operator_state": operator_state,
        "session_status": "OPEN" if active_window else ("CLOSED" if next_window else "NO_WINDOW"),
        "working_orders_count": int(working_orders_count),
        "open_positions_count": int(open_positions_count),
        "active_window": active_window,
        "next_window": next_window,
        "active_engines": active_engines,
        "blocker_code": blocker_code,
        "blocker_reason": blocker_reason,
        "blocker_instrument": latest_blocker.instrument if latest_blocker else None,
        "blocker_at": latest_blocker.created_at if latest_blocker else None,
    }


@router.get("/system/homepage-snapshot")
async def get_homepage_snapshot(session: AsyncSession = Depends(get_db)):
    now_utc = utcnow()

    operator = await get_operator_state(session)

    recent_signals = (
        await session.execute(
            select(Signal).order_by(desc(Signal.created_at)).limit(12)
        )
    ).scalars().all()

    working_orders = (
        await session.execute(
            select(Order)
            .where(Order.state.in_(_WORKING_ORDER_STATES))
            .order_by(desc(Order.created_at))
            .limit(12)
        )
    ).scalars().all()

    open_positions = (
        await session.execute(
            select(Position)
            .where(Position.status == "OPEN")
            .order_by(desc(Position.opened_at))
            .limit(12)
        )
    ).scalars().all()

    recent_trades = (
        await session.execute(
            select(Trade).order_by(desc(Trade.closed_at)).limit(8)
        )
    ).scalars().all()

    recent_events = (
        await session.execute(
            select(SystemEvent)
            .order_by(desc(SystemEvent.event_at))
            .limit(24)
        )
    ).scalars().all()

    regime_subq = (
        select(
            RegimeHistory.instrument,
            func.max(RegimeHistory.time).label("max_time"),
        )
        .where(RegimeHistory.instrument.in_(settings.instruments))
        .group_by(RegimeHistory.instrument)
        .subquery()
    )
    regimes = (
        await session.execute(
            select(RegimeHistory).join(
                regime_subq,
                (RegimeHistory.instrument == regime_subq.c.instrument)
                & (RegimeHistory.time == regime_subq.c.max_time),
            )
        )
    ).scalars().all()

    upcoming_calendar = (
        await session.execute(
            select(EconomicEvent)
            .where(
                EconomicEvent.event_time >= now_utc,
                EconomicEvent.event_time <= now_utc + timedelta(hours=48),
                EconomicEvent.impact.in_(["HIGH", "MEDIUM"]),
            )
            .order_by(EconomicEvent.event_time)
            .limit(4)
        )
    ).scalars().all()

    from anchor.signals.lcr_pair_status import load_lcr_pair_statuses
    pair_statuses = await load_lcr_pair_statuses(session)
    pair_rows = list(pair_statuses.values())

    activity = [
        *[_signal_activity(signal) for signal in recent_signals],
        *[_order_activity(order) for order in working_orders],
        *[_trade_activity(trade) for trade in recent_trades],
        *[_system_event_activity(event) for event in recent_events if _is_interesting_system_event(event)],
    ]
    activity.sort(key=lambda row: row["occurred_at"], reverse=True)
    activity = _collapse_activity(activity)

    regime_map = {row.instrument: row for row in regimes}
    watchlist = []
    for pair in pair_rows[:6]:
        regime = regime_map.get(pair.instrument)
        watchlist.append({
            "instrument": pair.instrument,
            "status": pair.status.value,
            "reason_codes": pair.reasons,
            "regime": regime.regime if regime else None,
            "confidence": float(regime.confidence) if regime and regime.confidence is not None else None,
        })

    if not watchlist:
        for regime in regimes[:6]:
            watchlist.append({
                "instrument": regime.instrument,
                "status": "watchlist",
                "reason_codes": [],
                "regime": regime.regime,
                "confidence": float(regime.confidence) if regime.confidence is not None else None,
            })

    return {
        "as_of": now_utc.isoformat(),
        "operator": operator,
        "account": {
          "mode": "paper" if settings.oanda_environment == "practice" else "live",
          "environment": settings.oanda_environment,
          "balance": _account_balance,
          "equity": _account_equity,
          "today_pl": (_account_equity - _account_balance) if _account_balance else 0.0,
          "stream_connected": _stream_connected,
          "last_reconciliation": _last_reconciliation,
        },
        "activity": activity[:14],
        "strategies": _strategy_states(operator.get("active_window")),
        "exposure": {
            "positions": [
                {
                    "id": str(position.id),
                    "instrument": position.instrument,
                    "direction": position.direction,
                    "units": float(position.units),
                    "avg_entry_price": float(position.avg_entry_price),
                    "current_price": float(position.current_price) if position.current_price is not None else None,
                    "unrealized_pl": float(position.unrealized_pl) if position.unrealized_pl is not None else None,
                    "stop_loss": float(position.stop_loss) if position.stop_loss is not None else None,
                    "take_profit": float(position.take_profit) if position.take_profit is not None else None,
                    "opened_at": position.opened_at.isoformat(),
                }
                for position in open_positions
            ],
            "orders": [
                {
                    "id": str(order.id),
                    "instrument": order.instrument,
                    "direction": order.direction,
                    "order_type": order.order_type,
                    "units": float(order.requested_units),
                    "state": order.state,
                    "created_at": order.created_at.isoformat(),
                    "stop_loss": float(order.stop_loss) if order.stop_loss is not None else None,
                    "take_profit": float(order.take_profit) if order.take_profit is not None else None,
                }
                for order in working_orders
            ],
        },
        "watchlist": watchlist,
        "recent_results": [
            {
                "id": str(trade.id),
                "instrument": trade.instrument,
                "direction": trade.direction,
                "opened_at": trade.opened_at.isoformat(),
                "closed_at": trade.closed_at.isoformat(),
                "net_pl": float(trade.net_pl),
                "close_reason": trade.close_reason,
            }
            for trade in recent_trades
        ],
        "calendar": [
            {
                "event_time": event.event_time.isoformat(),
                "currency": event.currency,
                "impact": event.impact,
                "event_name": event.event_name,
            }
            for event in upcoming_calendar
        ],
    }


@router.get("/system/config", response_model=RolloutConfigResponse)
async def get_system_config():
    """Return the active trading rollout configuration."""
    return {
        "account_mode": "paper" if settings.oanda_environment == "practice" else "live",
        "account_environment": settings.oanda_environment,
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
