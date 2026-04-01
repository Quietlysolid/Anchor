from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from anchor.build_info import DEPLOYED_AT, DEPLOYED_SHA
from anchor.config import get_settings
from anchor.database.engine import get_db
from anchor.api.schemas import (
    OperatorStateResponse,
    RolloutConfigResponse,
)
from anchor.database.models import Order, Position, Signal, SystemEvent, Trade
from anchor.execution.broker_client import BrokerClient
from anchor.futures.strategy import build_futures_v1_targets
from anchor.utils.time_utils import utcnow

router = APIRouter()
settings = get_settings()
logger = structlog.get_logger(__name__)

# Set by main.py lifespan after stream starts
_stream_connected: bool = False
_account_balance: float = 0.0
_account_equity: float = 0.0
_last_reconciliation: str | None = None
_open_positions_count: int = 0
_cached_positions: list = []
_cached_orders: list = []
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


def set_open_positions_count(count: int) -> None:
    global _open_positions_count
    _open_positions_count = count


def set_cached_broker_state(positions: list, orders: list) -> None:
    global _cached_positions, _cached_orders
    _cached_positions = positions
    _cached_orders = orders


def get_cached_positions() -> list:
    return _cached_positions


def get_cached_orders() -> list:
    return _cached_orders


def _window_payload(engine: str, label: str, starts_at: datetime, ends_at: datetime) -> dict:
    return {
        "engine": engine,
        "label": label,
        "starts_at": starts_at,
        "ends_at": ends_at,
    }


async def _broker_runtime_snapshot() -> dict:
    broker = BrokerClient()
    account = await broker.get_account_summary()
    positions = await broker.get_open_positions()
    pending_orders = await broker.get_pending_orders()
    account["openTradeCount"] = len(positions)
    return {
        "account": account,
        "positions": positions,
        "pending_orders": pending_orders,
    }


def _instrument_label(raw: str) -> str:
    return raw.replace("_", "/")


def _next_futures_rebalance(now_utc: datetime) -> datetime:
    hour, minute = [int(part) for part in settings.futures_daily_signal_time_utc.split(":", 1)]
    candidate = now_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
    while candidate <= now_utc or candidate.weekday() >= 5:
        candidate += timedelta(days=1)
        candidate = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate


def _futures_window(now_utc: datetime) -> tuple[dict | None, dict | None]:
    if not settings.futures_v1_enabled:
        return None, None
    next_rebalance = _next_futures_rebalance(now_utc)
    active = _window_payload(
        "futures_v1",
        "Futures portfolio",
        now_utc - timedelta(hours=1),
        next_rebalance,
    )
    return active, _window_payload(
        "futures_v1",
        "Next rebalance",
        next_rebalance,
        next_rebalance + timedelta(minutes=15),
    )


def _live_position_payload(position: dict) -> dict:
    current_units = float(position.get("currentUnits", 0.0) or 0.0)
    return {
        "id": str(position.get("id") or position.get("instrument")),
        "instrument": str(position.get("instrument", "")),
        "direction": "LONG" if current_units > 0 else "SHORT",
        "units": abs(current_units),
        "avg_entry_price": float(position.get("price", 0.0) or 0.0),
        "current_price": None,
        "unrealized_pl": float(position.get("unrealizedPL", 0.0) or 0.0),
        "stop_loss": None,
        "take_profit": None,
        "opened_at": _last_reconciliation or utcnow().isoformat(),
    }


def _live_order_payload(order: dict) -> dict:
    return {
        "id": str(order.get("id")),
        "instrument": str(order.get("instrument", "")),
        "direction": str(order.get("direction", "LONG")),
        "order_type": str(order.get("order_type", "MARKET")),
        "units": float(order.get("units", 0.0) or 0.0),
        "state": str(order.get("state", "")),
        "created_at": utcnow().isoformat(),
        "stop_loss": None,
        "take_profit": None,
    }


def _candidate_windows(now_utc: datetime) -> list[dict]:
    return []


async def _current_day_pl(session: AsyncSession) -> float | None:
    from anchor.database.models import EquityCurvePoint

    now_utc = utcnow()
    now_et = now_utc.astimezone(ZoneInfo("America/New_York"))
    et_midnight = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_midnight = et_midnight.astimezone(ZoneInfo("UTC"))

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

    if start_of_day_equity is None:
        return None
    return _account_equity - float(start_of_day_equity)


def _active_and_next_window(now_utc: datetime) -> tuple[dict | None, dict | None]:
    return _futures_window(now_utc)


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
    return [{
        "engine": "futures_v1",
        "label": "Trend / TSMOM",
        "status": "running" if settings.futures_v1_enabled else "off",
        "paper_only": settings.futures_v1_paper_only,
        "readiness": "paper_trial" if settings.futures_v1_paper_only else "live_ready",
        "readiness_label": "paper portfolio" if settings.futures_v1_paper_only else "live-ready",
    }]


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
    if settings.trading_domain == "futures" and event_type == "STARTUP_DIAGNOSTICS":
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
    from anchor.database.models import Position

    now_utc = utcnow()

    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    open_positions = 0
    if settings.trading_domain == "futures":
        # Use the cached count updated by _reconcile_account every 30s.
        # Avoids making a live broker call on every health check, which
        # caused thread-pool exhaustion and request pileup.
        open_positions = _open_positions_count
    else:
        try:
            result = await session.execute(
                select(func.count()).select_from(Position).where(Position.status == "OPEN")
            )
            open_positions = result.scalar() or 0
        except Exception:
            open_positions = 0

    today_pl = await _current_day_pl(session)

    return {
        "status":              "ok" if db_ok else "degraded",
        "db":                  "ok" if db_ok else "error",
        "timestamp":           now_utc.isoformat(),
        "deployed_sha":        DEPLOYED_SHA,
        "deployed_at":         DEPLOYED_AT,
        "broker_provider":     settings.broker_provider,
        "account_mode":        settings.account_mode,
        "account_environment": settings.account_environment,
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

    if settings.trading_domain == "futures":
        try:
            runtime = await _broker_runtime_snapshot()
            open_positions_count = len(runtime["positions"])
            working_orders_count = len(runtime["pending_orders"])
        except Exception:
            open_positions_count = _open_positions_count
            working_orders_count = 0
        latest_blocker = None
    else:
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

    active_engines = (
        ["futures_v1"]
        if settings.trading_domain == "futures" and settings.futures_v1_enabled
        else [
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
    )

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
    today_pl = await _current_day_pl(session)

    operator = await get_operator_state(session)

    if settings.trading_domain == "futures":
        runtime = {"positions": _cached_positions, "pending_orders": _cached_orders, "account": {}}
    else:
        runtime = None

    recent_signals = []
    if settings.trading_domain != "futures":
        recent_signals = (
            await session.execute(
                select(Signal).order_by(desc(Signal.created_at)).limit(12)
            )
        ).scalars().all()

    if settings.trading_domain == "futures":
        working_orders = []
        open_positions = []
    else:
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

    recent_trades = []
    if settings.trading_domain != "futures":
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

    upcoming_calendar = []

    activity = [
        *([] if settings.trading_domain == "futures" else [_signal_activity(signal) for signal in recent_signals]),
        *[_order_activity(order) for order in working_orders],
        *[_trade_activity(trade) for trade in recent_trades],
        *[_system_event_activity(event) for event in recent_events if _is_interesting_system_event(event)],
    ]
    activity.sort(key=lambda row: row["occurred_at"], reverse=True)
    activity = _collapse_activity(activity)

    if settings.trading_domain == "futures":
        target_map = {target.market: target for target in build_futures_v1_targets("/app/data", markets=list(settings.futures_v1_markets))}
        live_markets = {str(position.get("instrument", "")).split("-", 1)[0] for position in runtime["positions"]}
        watchlist = []
        for market in settings.futures_v1_markets:
            target = target_map.get(market)
            reason_codes = []
            status = "active" if market in live_markets else "watchlist"
            if target:
                reason_codes.append(f"signal_{'long' if target.signal > 0 else 'short'}")
                reason_codes.append(f"weight_{abs(target.weight):.3f}")
            else:
                reason_codes.append("flat_signal")
            watchlist.append({
                "instrument": market,
                "status": status,
                "reason_codes": reason_codes,
                "regime": None,
                "confidence": abs(target.weight) if target else 0.0,
            })
    else:
        watchlist = []

    return {
        "as_of": now_utc.isoformat(),
        "operator": operator,
        "account": {
          "provider": settings.broker_provider,
          "mode": settings.account_mode,
          "environment": settings.account_environment,
          "balance": _account_balance,
          "equity": _account_equity,
          "today_pl": today_pl,
          "stream_connected": _stream_connected,
          "last_reconciliation": _last_reconciliation,
        },
        "history_notice": (
            "This dashboard is now futures-only. Closed trades will appear here once the futures paper track record builds."
            if settings.trading_domain == "futures"
            else "Closed-trade history still comes from Anchor's local audit database. Open positions and working orders come directly from IBKR futures paper."
        ),
        "activity": activity[:14],
        "strategies": _strategy_states(operator.get("active_window")),
        "exposure": {
            "positions": (
                [_live_position_payload(position) for position in runtime["positions"]]
                if settings.trading_domain == "futures"
                else [
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
                ]
            ),
            "orders": (
                [_live_order_payload(order) for order in runtime["pending_orders"]]
                if settings.trading_domain == "futures"
                else [
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
                ]
            ),
        },
        "watchlist": watchlist,
        "recent_results": (
            []
            if settings.trading_domain == "futures"
            else [
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
            ]
        ),
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
        "account_mode": settings.account_mode,
        "account_environment": settings.account_environment,
        "instruments": settings.futures_v1_markets if settings.trading_domain == "futures" else settings.instruments,
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
