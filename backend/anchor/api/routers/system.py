from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

try:
    from anchor.build_info import DEPLOYED_AT, DEPLOYED_SHA
except ImportError:
    DEPLOYED_SHA = "unknown"
    DEPLOYED_AT = "unknown"
from anchor.config import get_settings
from anchor.database.engine import get_db
from anchor.api.schemas import (
    OperatorStateResponse,
    RolloutConfigResponse,
)
from anchor.database.models import Fill, Order, Position, Signal, SystemEvent, Trade
from anchor.execution.broker_client import BrokerClient
from anchor.futures.io import load_daily_market_closes
from anchor.futures.strategy import build_futures_v1_targets
from anchor.utils.time_utils import utcnow

router = APIRouter()
settings = get_settings()
logger = structlog.get_logger(__name__)

# Set by main.py lifespan after stream starts
_stream_connected: bool = False
_account_balance: float = 0.0
_account_equity: float = 0.0
_broker_day_pl: float | None = None
_account_available_funds: float = 0.0
_account_excess_liquidity: float = 0.0
_account_init_margin_req: float = 0.0
_account_maint_margin_req: float = 0.0
_last_reconciliation: str | None = None
_open_positions_count: int = 0
_cached_positions: list = []
_cached_orders: list = []
_WORKING_ORDER_STATES = ("PENDING", "SUBMITTED", "ACKNOWLEDGED", "PARTIAL")


def set_stream_status(connected: bool) -> None:
    global _stream_connected
    _stream_connected = connected


def set_account_info(
    balance: float,
    equity: float,
    reconciled_at: str | None = None,
    broker_day_pl: float | None = None,
    available_funds: float | None = None,
    excess_liquidity: float | None = None,
    init_margin_req: float | None = None,
    maint_margin_req: float | None = None,
) -> None:
    global _account_balance, _account_equity, _last_reconciliation, _broker_day_pl
    global _account_available_funds, _account_excess_liquidity, _account_init_margin_req, _account_maint_margin_req
    _account_balance = balance
    _account_equity = equity
    _broker_day_pl = broker_day_pl
    _account_available_funds = float(available_funds or 0.0)
    _account_excess_liquidity = float(excess_liquidity or 0.0)
    _account_init_margin_req = float(init_margin_req or 0.0)
    _account_maint_margin_req = float(maint_margin_req or 0.0)
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


def _matches_futures_market(instrument: str, markets: list[str]) -> bool:
    root = str(instrument or "").split("-", 1)[0]
    return root in set(markets)


def _next_futures_rebalance(now_utc: datetime) -> datetime:
    hour, minute = [int(part) for part in settings.futures_daily_signal_time_utc.split(":", 1)]
    frequency = settings.futures_rebalance_frequency.strip().lower()
    candidate = now_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if frequency == "daily":
        while candidate <= now_utc or candidate.weekday() >= 5:
            candidate += timedelta(days=1)
            candidate = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate

    if frequency == "weekly":
        while candidate <= now_utc or candidate.weekday() != 0:
            candidate += timedelta(days=1)
            candidate = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate

    raise ValueError(f"Unsupported futures_rebalance_frequency: {settings.futures_rebalance_frequency}")


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
    market_price = float(position.get("marketPrice", 0.0) or 0.0) or None
    return {
        "id": str(position.get("id") or position.get("instrument")),
        "instrument": str(position.get("instrument", "")),
        "direction": "LONG" if current_units > 0 else "SHORT",
        "units": abs(current_units),
        "avg_entry_price": float(position.get("price", 0.0) or 0.0),
        "current_price": market_price,
        "unrealized_pl": float(position.get("unrealizedPL", 0.0) or 0.0),
        "stop_loss": None,
        "take_profit": None,
        "opened_at": _last_reconciliation or utcnow().isoformat(),
    }


def _market_data_mode_label(mode: int) -> str:
    return {
        1: "live",
        2: "frozen",
        3: "delayed",
        4: "delayed-frozen",
    }.get(mode, "unknown")


def _title_case_word(value: str) -> str:
    if not value:
        return "Unknown"
    return value[:1].upper() + value[1:]


def _margin_usage_pct() -> float | None:
    if _account_equity <= 0 or _account_maint_margin_req <= 0:
        return None
    return (_account_maint_margin_req / _account_equity) * 100.0


def _percent_return(current: float, baseline: float | None) -> float | None:
    if baseline is None or baseline == 0:
        return None
    return ((current / baseline) - 1.0) * 100.0


def _window_baseline(points: list, cutoff: datetime) -> float | None:
    for point in points:
        if point.time >= cutoff:
            return float(point.account_equity)
    return float(points[0].account_equity) if points else None


def _readiness_incident_cutoff(now_utc: datetime) -> datetime:
    rolling_cutoff = now_utc - timedelta(days=30)
    if DEPLOYED_AT == "unknown":
        return rolling_cutoff
    try:
        deployed_at = datetime.fromisoformat(DEPLOYED_AT.replace("Z", "+00:00"))
    except ValueError:
        return rolling_cutoff
    return max(rolling_cutoff, deployed_at)


async def _performance_snapshot(session: AsyncSession, now_utc: datetime) -> dict:
    from anchor.database.models import EquityCurvePoint

    equity_rows = (
        await session.execute(
            select(EquityCurvePoint)
            .order_by(desc(EquityCurvePoint.time))
            .limit(240)
        )
    ).scalars().all()
    points = [point for point in reversed(equity_rows) if float(point.account_equity or 0.0) > 0.0]

    current_equity = _account_equity or (float(points[-1].account_equity) if points else 0.0)
    if not points:
        return {
            "equity_curve": [],
            "returns": {"1w_pct": None, "mtd_pct": None, "since_start_pct": None},
            "drawdown": {"current_pct": None, "max_pct": None},
            "realized_pl": {"mtd": None, "since_start": None},
            "unrealized_pl": None,
            "track_record_days": 0,
        }

    now_et = now_utc.astimezone(ZoneInfo("America/New_York"))
    week_cutoff = now_utc - timedelta(days=7)
    month_start_et = now_et.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start_utc = month_start_et.astimezone(ZoneInfo("UTC"))

    one_week_baseline = _window_baseline(points, week_cutoff)
    month_baseline = _window_baseline(points, month_start_utc)
    since_start_baseline = float(points[0].account_equity)

    realized_since_start = await session.scalar(select(func.coalesce(func.sum(Trade.net_pl), 0.0)))
    realized_mtd = await session.scalar(
        select(func.coalesce(func.sum(Trade.net_pl), 0.0)).where(Trade.closed_at >= month_start_utc)
    )

    max_drawdown = max(
        (float(point.drawdown_pct) for point in points if point.drawdown_pct is not None),
        default=None,
    )
    current_drawdown = float(points[-1].drawdown_pct) if points[-1].drawdown_pct is not None else None
    unrealized_pl = sum(float(position.get("unrealizedPL", 0.0) or 0.0) for position in _cached_positions)

    return {
        "equity_curve": [
            {
                "time": point.time.isoformat(),
                "account_balance": float(point.account_balance),
                "account_equity": float(point.account_equity),
                "drawdown_pct": float(point.drawdown_pct) if point.drawdown_pct is not None else None,
            }
            for point in points
        ],
        "returns": {
            "1w_pct": _percent_return(current_equity, one_week_baseline),
            "mtd_pct": _percent_return(current_equity, month_baseline),
            "since_start_pct": _percent_return(current_equity, since_start_baseline),
        },
        "drawdown": {
            "current_pct": current_drawdown,
            "max_pct": max_drawdown,
        },
        "realized_pl": {
            "mtd": float(realized_mtd or 0.0),
            "since_start": float(realized_since_start or 0.0),
        },
        "unrealized_pl": unrealized_pl,
        "track_record_days": max(1, (now_utc.date() - points[0].time.date()).days + 1),
    }


async def _readiness_snapshot(session: AsyncSession, performance: dict, now_utc: datetime) -> dict:
    track_record_days = int(performance.get("track_record_days") or 0)
    max_drawdown_pct = performance.get("drawdown", {}).get("max_pct")
    since_start_return_pct = performance.get("returns", {}).get("since_start_pct")
    incident_cutoff = _readiness_incident_cutoff(now_utc)
    rebalance_events = (
        await session.execute(
            select(SystemEvent.event_at, SystemEvent.metadata_)
            .where(SystemEvent.event_type == "FUTURES_V1_REBALANCE_PLAN")
        )
    ).all()
    order_times = (
        await session.execute(select(Order.created_at).order_by(Order.created_at))
    ).scalars().all()
    fill_times = (
        await session.execute(select(Fill.fill_at).order_by(Fill.fill_at))
    ).scalars().all()
    rebalance_count = sum(
        1
        for event_at, metadata in rebalance_events
        if isinstance(metadata, dict)
        and _is_successful_auto_rebalance(metadata)
        and _has_rebalance_execution_evidence(event_at, order_times, fill_times)
    )
    margin_incidents = int(
        await session.scalar(
            select(func.count())
            .select_from(SystemEvent)
            .where(
                SystemEvent.event_at >= incident_cutoff,
                SystemEvent.event_type.in_(["FUTURES_MARGIN_GUARD", "FUTURES_DRAWDOWN_GUARD"]),
            )
        ) or 0
    )
    operational_incidents = int(
        await session.scalar(
            select(func.count())
            .select_from(SystemEvent)
            .where(
                SystemEvent.event_at >= incident_cutoff,
                SystemEvent.severity.in_(["ERROR", "CRITICAL"]),
            )
        ) or 0
    )

    criteria = [
        {
            "key": "track_record",
            "label": "Track record",
            "value": f"{track_record_days}d",
            "target": f">= {settings.futures_readiness_min_track_record_days}d",
            "passed": track_record_days >= settings.futures_readiness_min_track_record_days,
        },
        {
            "key": "rebalances",
            "label": "Automated rebalance cycles",
            "value": str(rebalance_count),
            "target": f">= {settings.futures_readiness_min_rebalances}",
            "passed": rebalance_count >= settings.futures_readiness_min_rebalances,
        },
        {
            "key": "drawdown",
            "label": "Max drawdown",
            "value": "—" if max_drawdown_pct is None else f"{float(max_drawdown_pct):.1f}%",
            "target": f"<= {settings.futures_readiness_max_drawdown_pct:.1f}%",
            "passed": max_drawdown_pct is not None and float(max_drawdown_pct) <= settings.futures_readiness_max_drawdown_pct,
        },
        {
            "key": "return",
            "label": "Since-start return",
            "value": "—" if since_start_return_pct is None else f"{float(since_start_return_pct):.1f}%",
            "target": f">= {settings.futures_readiness_min_since_start_return_pct:.1f}%",
            "passed": since_start_return_pct is not None and float(since_start_return_pct) >= settings.futures_readiness_min_since_start_return_pct,
        },
        {
            "key": "margin_incidents",
            "label": "Margin incidents (30d)",
            "value": str(margin_incidents),
            "target": f"<= {settings.futures_readiness_max_margin_incidents_30d}",
            "passed": margin_incidents <= settings.futures_readiness_max_margin_incidents_30d,
        },
        {
            "key": "ops_incidents",
            "label": "Operational incidents (30d)",
            "value": str(operational_incidents),
            "target": f"<= {settings.futures_readiness_max_operational_incidents_30d}",
            "passed": operational_incidents <= settings.futures_readiness_max_operational_incidents_30d,
        },
    ]
    passed_count = sum(1 for criterion in criteria if criterion["passed"])
    failing_labels = [criterion["label"] for criterion in criteria if not criterion["passed"]]

    if track_record_days < 14 or rebalance_count < 1:
        state = "observe"
        label = "Observe"
        recommendation = "Keep paper trading until the bot has more history and at least one successful automated rebalance cycle."
    elif operational_incidents > settings.futures_readiness_max_operational_incidents_30d or margin_incidents > settings.futures_readiness_max_margin_incidents_30d:
        state = "not_ready"
        label = "Not ready"
        recommendation = "Do not mirror to live until recent incidents and guardrail triggers are resolved."
    elif all(criterion["passed"] for criterion in criteria):
        state = "candidate_for_live_mirror"
        label = "Candidate for live mirror"
        recommendation = "Paper performance and operations meet the current live-mirroring bar."
    else:
        state = "paper_validated"
        label = "Paper validated"
        recommendation = "Paper performance is promising, but the remaining failed checks should clear before live mirroring."

    return {
        "state": state,
        "label": label,
        "recommendation": recommendation,
        "incident_cutoff": incident_cutoff.isoformat(),
        "track_record_days": track_record_days,
        "rebalance_count": rebalance_count,
        "margin_incidents": margin_incidents,
        "operational_incidents": operational_incidents,
        "criteria": criteria,
        "passed_checks": passed_count,
        "total_checks": len(criteria),
        "failing_checks": failing_labels,
    }


def _decision_rows(activity: list[dict]) -> list[dict]:
    return [
        {
            "id": row["id"],
            "occurred_at": row["occurred_at"],
            "tone": row["tone"],
            "title": row["title"],
            "detail": row["detail"],
            "reason_code": row["reason_code"],
        }
        for row in activity[:6]
    ]


def _event_metadata(event: SystemEvent) -> dict:
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    return metadata


def _is_successful_auto_rebalance(metadata: dict) -> bool:
    auto_execute = bool(metadata.get("auto_execute"))
    blocked_reason = metadata.get("blocked_reason")
    execution_errors = metadata.get("execution_errors")
    actions = metadata.get("actions")

    if isinstance(actions, list):
        action_count = len(actions)
    else:
        action_count = int(actions or 0)

    if not auto_execute or blocked_reason:
        return False
    if action_count <= 0:
        return False
    if isinstance(execution_errors, list) and execution_errors:
        return False
    return True


def _has_rebalance_execution_evidence(
    event_at: datetime,
    order_times: list[datetime],
    fill_times: list[datetime],
) -> bool:
    window_end = event_at + timedelta(minutes=30)
    for ts in order_times:
        if event_at <= ts <= window_end:
            return True
    for ts in fill_times:
        if event_at <= ts <= window_end:
            return True
    return False


def _rebalance_event_detail(event: SystemEvent) -> tuple[str, str]:
    metadata = _event_metadata(event)
    actions = metadata.get("actions")
    blocked_reason = metadata.get("blocked_reason")
    auto_execute = metadata.get("auto_execute")

    if isinstance(actions, list):
        action_count = len(actions)
    else:
        action_count = int(actions or 0)

    title = f"Rebalance plan: {action_count} action{'s' if action_count != 1 else ''}"
    detail_bits: list[str] = []
    if blocked_reason:
        detail_bits.append(f"blocked: {blocked_reason}")
    elif auto_execute:
        detail_bits.append("recorded for automatic execution")
    else:
        detail_bits.append("plan recorded")
    markets = metadata.get("markets")
    if isinstance(markets, list) and markets:
        detail_bits.append(", ".join(str(market) for market in markets[:4]))
    return title, " · ".join(detail_bits)


def _trade_plan_snapshot(event: SystemEvent | None) -> dict | None:
    if event is None:
        return None

    metadata = event.metadata_ if isinstance(event.metadata_, dict) else {}
    actions = metadata.get("actions")
    action_rows = actions if isinstance(actions, list) else []

    return {
        "generated_at": event.event_at.isoformat(),
        "blocked_reason": metadata.get("blocked_reason"),
        "auto_execute": bool(metadata.get("auto_execute")),
        "actions": [
            {
                "action": str(action.get("action") or ""),
                "market": str(action.get("market") or ""),
                "instrument": str(action.get("instrument") or ""),
                "contracts": int(action.get("contracts") or 0),
                "direction": str(action.get("direction") or ""),
                "reason": str(action.get("reason") or ""),
            }
            for action in action_rows
            if isinstance(action, dict)
        ],
    }


def _latest_market_close(instrument: str) -> float | None:
    market_id = str(instrument).upper().split("-", 1)[0]
    try:
        closes = load_daily_market_closes(data_dir="/app/data", markets=[market_id])[market_id].dropna()
    except Exception:
        return None
    if closes.empty:
        return None
    return float(closes.iloc[-1])


def _manual_trade_stop_loss(instrument: str, direction: str, reference_price: float | None) -> float | None:
    if reference_price is None or reference_price <= 0:
        return None

    market_id = str(instrument).upper().split("-", 1)[0]
    try:
        closes = load_daily_market_closes(data_dir="/app/data", markets=[market_id])[market_id].dropna()
    except Exception:
        return None
    returns = closes.pct_change(fill_method=None).dropna()
    history = returns.tail(max(settings.futures_vol_lookback_days, 5))
    if history.empty:
        return None

    daily_vol = float(history.std(ddof=0) or 0.0)
    if daily_vol <= 0:
        return None

    stop_distance = reference_price * daily_vol * 2.5
    if direction == "LONG":
        return max(0.0, reference_price - stop_distance)
    return reference_price + stop_distance


def _trade_plan_action_payload(action: dict, next_rebalance_at: datetime) -> dict:
    instrument = str(action.get("instrument") or "")
    direction = str(action.get("direction") or "")
    reason = str(action.get("reason") or "")
    action_type = str(action.get("action") or "")
    reference_price = _latest_market_close(instrument)
    emergency_stop = (
        _manual_trade_stop_loss(instrument, direction, reference_price)
        if action_type == "open"
        else None
    )

    if action_type == "close":
        entry_note = "Close now at market."
        hold_note = "Exit this position now."
        exit_note = "Position should be flat after you close it."
    else:
        entry_note = "Enter now at market."
        hold_note = f"Hold until the next scheduled rebalance on {next_rebalance_at.isoformat()} unless a stop or guardrail triggers first."
        exit_note = "Exit earlier if the emergency stop is hit or a guardrail forces de-risking."

    return {
        "action": action_type,
        "market": str(action.get("market") or ""),
        "instrument": instrument,
        "contracts": int(action.get("contracts") or 0),
        "direction": direction,
        "reason": reason,
        "entry_note": entry_note,
        "reference_price": reference_price,
        "emergency_stop": emergency_stop,
        "hold_note": hold_note,
        "exit_note": exit_note,
    }


def _manual_trade_plan_snapshot(event: SystemEvent | None, now_utc: datetime) -> dict | None:
    base = _trade_plan_snapshot(event)
    if base is None:
        return None

    next_rebalance_at = _next_futures_rebalance(now_utc)
    return {
        **base,
        "next_rebalance_at": next_rebalance_at.isoformat(),
        "actions": [
            _trade_plan_action_payload(action, next_rebalance_at)
            for action in base["actions"]
        ],
    }


def _guardrail_event_detail(event: SystemEvent) -> tuple[str, str]:
    metadata = _event_metadata(event)
    event_type = event.event_type.upper()

    if event_type == "FUTURES_MARGIN_GUARD":
        title = "Margin guard active"
        usage = metadata.get("live_margin_usage_pct")
        threshold = metadata.get("block_new_opens_threshold") or metadata.get("force_derisk_threshold")
        instrument = metadata.get("instrument")
        detail_bits: list[str] = []
        if usage is not None:
            detail_bits.append(f"usage {float(usage) * 100:.1f}%")
        if threshold is not None:
            detail_bits.append(f"threshold {float(threshold) * 100:.1f}%")
        if instrument:
            detail_bits.append(str(instrument))
        return title, " · ".join(detail_bits) or event.message

    if event_type == "FUTURES_DRAWDOWN_GUARD":
        title = "Drawdown guard active"
        detail_bits = []
        reason = metadata.get("drawdown_reason")
        drawdown_pct = metadata.get("drawdown_pct")
        if reason:
            detail_bits.append(str(reason))
        if drawdown_pct is not None:
            detail_bits.append(f"drawdown {float(drawdown_pct):.1f}%")
        return title, " · ".join(detail_bits) or event.message

    return event.message, event.event_type


def _reconciliation_event_detail(event: SystemEvent) -> tuple[str, str]:
    metadata = _event_metadata(event)
    actions_taken = metadata.get("actions_taken")
    missing_from_broker = metadata.get("missing_from_broker")
    missing_from_db = metadata.get("missing_from_db")
    audit_gaps = metadata.get("audit_gaps")

    action_count = len(actions_taken) if isinstance(actions_taken, list) else int(actions_taken or 0)
    missing_broker_count = len(missing_from_broker) if isinstance(missing_from_broker, list) else 0
    missing_db_count = len(missing_from_db) if isinstance(missing_from_db, list) else 0
    audit_gap_count = 0
    if isinstance(audit_gaps, dict):
        audit_gap_count = sum(int(bool(value)) for value in audit_gaps.values())

    if action_count == 0 and missing_broker_count == 0 and missing_db_count == 0 and audit_gap_count == 0:
        return "Reconciliation check passed", "Broker and database matched. No fixes were needed."

    detail_bits: list[str] = []
    if action_count:
        detail_bits.append(f"{action_count} fix{'es' if action_count != 1 else ''} applied")
    if missing_broker_count:
        detail_bits.append(f"{missing_broker_count} position{'s' if missing_broker_count != 1 else ''} closed in Anchor")
    if missing_db_count:
        detail_bits.append(f"{missing_db_count} broker trade{'s' if missing_db_count != 1 else ''} restored")
    if audit_gap_count:
        detail_bits.append(f"{audit_gap_count} audit gap{'s' if audit_gap_count != 1 else ''} repaired")

    return "Reconciliation applied fixes", " · ".join(detail_bits) or event.message


def _drawdown_guard_snapshot(guardrail_events: list[SystemEvent], performance: dict) -> dict | None:
    current_drawdown_pct = performance.get("drawdown", {}).get("current_pct")
    if current_drawdown_pct is None:
        return None

    reduce_threshold_pct = settings.drawdown_reduce_pct * 100.0
    halt_threshold_pct = settings.drawdown_halt_pct * 100.0
    monthly_halt_threshold_pct = settings.monthly_halt_pct * 100.0
    latest_drawdown_event = next(
        (event for event in guardrail_events if event.event_type.upper() == "FUTURES_DRAWDOWN_GUARD"),
        None,
    )
    metadata = _event_metadata(latest_drawdown_event) if latest_drawdown_event is not None else {}
    reason = str(metadata.get("drawdown_reason") or "").strip() or None

    if reason == "MONTHLY_DRAWDOWN_HALT":
        return {
            "active": True,
            "mode": "monthly_halt",
            "title": "Drawdown guard halting new opens",
            "reason": "Month-to-date loss hit the monthly circuit-breaker limit.",
            "blocking": "New futures opens are halted for the rest of the current month.",
            "clear_when": "This clears when the next calendar month begins and the monthly loss limit resets.",
            "current_drawdown_pct": float(current_drawdown_pct),
            "reduce_threshold_pct": reduce_threshold_pct,
            "halt_threshold_pct": halt_threshold_pct,
            "monthly_halt_threshold_pct": monthly_halt_threshold_pct,
            "scale_factor": 0.5,
            "occurred_at": latest_drawdown_event.event_at.isoformat() if latest_drawdown_event is not None else None,
        }

    if float(current_drawdown_pct) >= halt_threshold_pct:
        return {
            "active": True,
            "mode": "halt",
            "title": "Drawdown guard halting new opens",
            "reason": reason or f"Portfolio drawdown is at {float(current_drawdown_pct):.1f}%, above the halt level.",
            "blocking": "New futures opens are halted while drawdown stays above the halt threshold.",
            "clear_when": f"New opens resume once drawdown recovers below {halt_threshold_pct:.1f}% from peak equity.",
            "current_drawdown_pct": float(current_drawdown_pct),
            "reduce_threshold_pct": reduce_threshold_pct,
            "halt_threshold_pct": halt_threshold_pct,
            "monthly_halt_threshold_pct": monthly_halt_threshold_pct,
            "scale_factor": 0.5,
            "occurred_at": latest_drawdown_event.event_at.isoformat() if latest_drawdown_event is not None else None,
        }

    if float(current_drawdown_pct) >= reduce_threshold_pct:
        return {
            "active": True,
            "mode": "reduced",
            "title": "Drawdown protection reducing size",
            "reason": reason or f"Portfolio drawdown is at {float(current_drawdown_pct):.1f}%, above the reduce-risk level.",
            "blocking": "New risk is scaled down to 50% while drawdown stays above the reduce threshold.",
            "clear_when": f"Full sizing resumes once drawdown recovers below {reduce_threshold_pct:.1f}% from peak equity.",
            "current_drawdown_pct": float(current_drawdown_pct),
            "reduce_threshold_pct": reduce_threshold_pct,
            "halt_threshold_pct": halt_threshold_pct,
            "monthly_halt_threshold_pct": monthly_halt_threshold_pct,
            "scale_factor": 0.5,
            "occurred_at": latest_drawdown_event.event_at.isoformat() if latest_drawdown_event is not None else None,
        }

    return None


def _status_snapshot(
    operator: dict,
    latest_rebalance: SystemEvent | None,
    guardrail_events: list[SystemEvent],
    performance: dict,
) -> dict:
    open_orders_count = int(operator.get("working_orders_count") or 0)
    open_positions_count = int(operator.get("open_positions_count") or 0)

    rebalance_payload = None
    if latest_rebalance is not None:
        title, detail = _rebalance_event_detail(latest_rebalance)
        rebalance_payload = {
            "occurred_at": latest_rebalance.event_at.isoformat(),
            "title": title,
            "detail": detail,
        }

    drawdown_guard = _drawdown_guard_snapshot(guardrail_events, performance)

    active_guardrails = []
    for event in guardrail_events[:3]:
        if event.event_type.upper() == "FUTURES_DRAWDOWN_GUARD" and drawdown_guard is None:
            continue
        title, detail = _guardrail_event_detail(event)
        active_guardrails.append({
            "event_type": event.event_type,
            "occurred_at": event.event_at.isoformat(),
            "title": title,
            "detail": detail,
        })

    return {
        "stream_connected": _stream_connected,
        "last_broker_sync": _last_reconciliation,
        "open_positions_count": open_positions_count,
        "open_orders_count": open_orders_count,
        "last_rebalance": rebalance_payload,
        "drawdown_guard": drawdown_guard,
        "active_guardrails": active_guardrails,
    }


def _alert_rows(
    *,
    operator: dict,
    account: dict,
    readiness: dict,
    status: dict,
    now_utc: datetime,
) -> list[dict]:
    alerts: list[dict] = []
    operator_state = str(operator.get("operator_state") or "")
    market_data_mode = str(account.get("market_data_mode") or "unknown")
    margin_usage_pct = account.get("margin_usage_pct")
    last_sync = status.get("last_broker_sync")

    if operator_state == "DEGRADED":
        alerts.append({
            "severity": "critical",
            "title": "Operator degraded",
            "detail": "Broker sync or database health needs attention before trusting automation.",
        })

    if not bool(status.get("stream_connected")):
        alerts.append({
            "severity": "critical",
            "title": "Broker stream disconnected",
            "detail": "Live broker updates are not flowing into Anchor right now.",
        })

    if market_data_mode != "live":
        alerts.append({
            "severity": "warning",
            "title": f"{_title_case_word(market_data_mode) if market_data_mode else 'Unknown'} market data mode",
            "detail": "Broker marks may lag the broker app until live data is active.",
        })

    if margin_usage_pct is not None and float(margin_usage_pct) >= settings.futures_margin_warn_usage_pct * 100.0:
        alerts.append({
            "severity": "warning",
            "title": "Margin usage elevated",
            "detail": f"Margin usage is {float(margin_usage_pct):.1f}% against a warning level of {settings.futures_margin_warn_usage_pct * 100:.1f}%.",
        })

    for guardrail in status.get("active_guardrails", []):
        alerts.append({
            "severity": "warning",
            "title": guardrail["title"],
            "detail": guardrail["detail"],
        })

    if last_sync:
        sync_age_seconds = (now_utc - datetime.fromisoformat(last_sync)).total_seconds()
        if sync_age_seconds > 90:
            alerts.append({
                "severity": "warning",
                "title": "Broker sync stale",
                "detail": f"Last broker sync was {int(sync_age_seconds)}s ago.",
            })

    if readiness.get("state") in {"observe", "not_ready"}:
        alerts.append({
            "severity": "info",
            "title": f"Paper readiness: {readiness.get('label', 'Observe')}",
            "detail": f"{readiness.get('passed_checks', 0)}/{readiness.get('total_checks', 0)} live-mirroring checks currently pass.",
        })

    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for alert in alerts:
        key = (str(alert["severity"]), str(alert["title"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(alert)
    return deduped[:6]


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


def _fill_payload(fill: Fill, order: Order | None) -> dict:
    order_direction = str(order.direction) if order is not None else "LONG"
    order_type = str(order.order_type) if order is not None else "MARKET"
    return {
        "id": str(fill.id),
        "instrument": str(fill.instrument),
        "direction": order_direction,
        "order_type": order_type,
        "units": float(fill.units_filled),
        "fill_price": float(fill.fill_price),
        "fill_at": fill.fill_at.isoformat(),
        "slippage_pips": float(fill.slippage_pips) if fill.slippage_pips is not None else None,
        "commission": float(fill.commission) if fill.commission is not None else 0.0,
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
    event_type = event.event_type.upper()
    if event_type == "FUTURES_V1_REBALANCE_PLAN":
        title, detail = _rebalance_event_detail(event)
    elif event_type == "RECONCILIATION":
        title, detail = _reconciliation_event_detail(event)
    elif event_type in {"FUTURES_MARGIN_GUARD", "FUTURES_DRAWDOWN_GUARD"}:
        title, detail = _guardrail_event_detail(event)
    else:
        title, detail = event.message, event.event_type
    return {
        "id": f"event-{event.id}",
        "occurred_at": event.event_at.isoformat(),
        "kind": "system_event",
        "tone": "bad" if sev == "ERROR" else "warn" if sev == "WARNING" else "info",
        "badge": sev,
        "instrument": None,
        "reason_code": event.event_type,
        "title": title,
        "detail": detail,
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
    return event_type in {
        "RECONCILIATION",
        "LIVE_PERF_CHECK",
        "EDGE_CONFIDENCE_CHECK",
        "FUTURES_V1_REBALANCE_PLAN",
    }


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
        "broker_day_pl":       _broker_day_pl,
        "today_pl":            today_pl,
        "available_funds":     _account_available_funds,
        "excess_liquidity":    _account_excess_liquidity,
        "initial_margin":      _account_init_margin_req,
        "maintenance_margin":  _account_maint_margin_req,
        "margin_usage_pct":    _margin_usage_pct(),
        "account_id":          settings.broker_account_id or None,
        "market_data_mode":    _market_data_mode_label(settings.ibkr_market_data_type),
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

    recent_trades_stmt = select(Trade).order_by(desc(Trade.closed_at)).limit(24)
    recent_trades_rows = (await session.execute(recent_trades_stmt)).scalars().all()
    if settings.trading_domain == "futures":
        recent_trades = [
            trade for trade in recent_trades_rows
            if abs(float(trade.net_pl or 0.0)) > 1e-9
        ][:8]
    else:
        recent_trades = recent_trades_rows[:8]

    recent_events = (
        await session.execute(
            select(SystemEvent)
            .order_by(desc(SystemEvent.event_at))
            .limit(24)
        )
    ).scalars().all()
    recent_fills_stmt = (
        select(Fill, Order)
        .outerjoin(Order, Order.id == Fill.order_id)
        .order_by(desc(Fill.fill_at))
        .limit(24)
    )
    recent_fills_rows = (await session.execute(recent_fills_stmt)).all()
    if settings.trading_domain == "futures":
        recent_fills = [
            (fill, order)
            for fill, order in recent_fills_rows
            if _matches_futures_market(fill.instrument, list(settings.futures_v1_markets))
        ][:8]
    else:
        recent_fills = recent_fills_rows[:8]
    latest_rebalance = (
        await session.execute(
            select(SystemEvent)
            .where(SystemEvent.event_type == "FUTURES_V1_REBALANCE_PLAN")
            .order_by(desc(SystemEvent.event_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    active_guardrails = (
        await session.execute(
            select(SystemEvent)
            .where(
                SystemEvent.event_type.in_(["FUTURES_MARGIN_GUARD", "FUTURES_DRAWDOWN_GUARD"]),
                SystemEvent.event_at >= now_utc - timedelta(days=7),
            )
            .order_by(desc(SystemEvent.event_at))
            .limit(3)
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
    performance = await _performance_snapshot(session, now_utc)
    readiness = await _readiness_snapshot(session, performance, now_utc)
    margin_usage_pct = _margin_usage_pct()
    account_snapshot = {
        "provider": settings.broker_provider,
        "mode": settings.account_mode,
        "environment": settings.account_environment,
        "account_id": settings.broker_account_id or None,
        "market_data_mode": _market_data_mode_label(settings.ibkr_market_data_type),
        "balance": _account_balance,
        "equity": _account_equity,
        "available_funds": _account_available_funds,
        "excess_liquidity": _account_excess_liquidity,
        "initial_margin": _account_init_margin_req,
        "maintenance_margin": _account_maint_margin_req,
        "margin_usage_pct": margin_usage_pct,
        "broker_day_pl": _broker_day_pl,
        "today_pl": _broker_day_pl,
        "anchor_day_pl": today_pl,
        "stream_connected": _stream_connected,
        "last_reconciliation": _last_reconciliation,
    }
    status = _status_snapshot(operator, latest_rebalance, active_guardrails, performance)
    alerts = _alert_rows(
        operator=operator,
        account=account_snapshot,
        readiness=readiness,
        status=status,
        now_utc=now_utc,
    )

    if settings.trading_domain == "futures":
        target_map = {target.market: target for target in build_futures_v1_targets("/app/data", markets=list(settings.futures_v1_markets))}
        live_markets = {str(position.get("instrument", "")).split("-", 1)[0] for position in runtime["positions"]}
        watchlist = []
        for market in settings.futures_v1_markets:
            target = target_map.get(market)
            reason_codes = []
            watch_status = "active" if market in live_markets else "watchlist"
            if target:
                reason_codes.append(f"signal_{'long' if target.signal > 0 else 'short'}")
                reason_codes.append(f"weight_{abs(target.weight):.3f}")
            else:
                reason_codes.append("flat_signal")
            watchlist.append({
                "instrument": market,
                "status": watch_status,
                "reason_codes": reason_codes,
                "regime": None,
                "confidence": abs(target.weight) if target else 0.0,
            })
    else:
        watchlist = []

    return {
        "as_of": now_utc.isoformat(),
        "operator": operator,
        "account": account_snapshot,
        "history_notice": (
            "This dashboard is now futures-only. Closed futures trades and fills come from Anchor's local audit history."
            if settings.trading_domain == "futures"
            else "Closed-trade history still comes from Anchor's local audit database. Open positions and working orders come directly from IBKR futures paper."
        ),
        "status": status,
        "trade_plan": _manual_trade_plan_snapshot(latest_rebalance, now_utc),
        "alerts": alerts,
        "activity": activity[:14],
        "decisions": _decision_rows(activity),
        "strategies": _strategy_states(operator.get("active_window")),
        "performance": performance,
        "readiness": readiness,
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
        "execution": {
            "open_orders": (
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
            "recent_fills": [
                _fill_payload(fill, order)
                for fill, order in recent_fills
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
