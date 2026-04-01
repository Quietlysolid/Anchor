"""Futures-specific scheduled tasks."""
from __future__ import annotations

import uuid

import structlog

from anchor.scheduler.celery_app import celery_app
from anchor.scheduler._shared import _drawdown_monitor, _run_async
from anchor.futures.contracts import get_futures_market
from anchor.futures.io import load_daily_market_closes

logger = structlog.get_logger(__name__)

def _account_margin_usage_pct(account: dict) -> float | None:
    nav = float(account.get("NAV", 0.0) or 0.0)
    maint = float(account.get("fullMaintMarginReq", account.get("maintMarginReq", 0.0)) or 0.0)
    if nav <= 0 or maint <= 0:
        return None
    return maint / nav


def _estimated_position_margin_burden(position: dict) -> float:
    instrument = str(position.get("instrument", "")).upper()
    market_id = instrument.split("-", 1)[0]
    try:
        market = get_futures_market(market_id)
    except KeyError:
        return 0.0

    units = abs(float(position.get("currentUnits", 0.0) or 0.0))
    market_price = float(position.get("marketPrice", 0.0) or 0.0)
    if units <= 0 or market_price <= 0:
        return 0.0
    notional_usd = units * market_price * market.point_value_usd
    return notional_usd * market.margin_requirement_pct_estimate


def _select_force_derisk_position(actual_positions: list[dict]) -> dict | None:
    ranked = sorted(
        actual_positions,
        key=_estimated_position_margin_burden,
        reverse=True,
    )
    for position in ranked:
        units = abs(float(position.get("currentUnits", 0.0) or 0.0))
        if units >= 1:
            return position
    return None


def _vol_based_emergency_stop(
    data_dir: str,
    instrument: str,
    direction: str,
    reference_price: float,
    vol_lookback_days: int,
) -> float | None:
    market_id = str(instrument).upper().split("-", 1)[0]
    if reference_price <= 0:
        return None
    try:
        closes = load_daily_market_closes(data_dir=data_dir, markets=[market_id])[market_id].dropna()
    except Exception:
        return None
    returns = closes.pct_change(fill_method=None).dropna()
    history = returns.tail(max(vol_lookback_days, 5))
    if history.empty:
        return None
    daily_vol = float(history.std(ddof=0) or 0.0)
    if daily_vol <= 0:
        return None
    stop_distance = reference_price * daily_vol * 2.5
    if direction == "LONG":
        return max(0.0, reference_price - stop_distance)
    return reference_price + stop_distance


@celery_app.task(name="anchor.scheduler.jobs.run_futures_v1_rebalance", bind=True, max_retries=2)
def run_futures_v1_rebalance(self):
    """Rebalance the Futures v1 paper portfolio against current IBKR positions."""

    async def _inner():
        from anchor.config import settings
        from anchor.database.engine import init_db
        from anchor.database.repositories.events import SystemEventRepository
        from anchor.execution.broker_client import BrokerClient
        from anchor.execution.order_types import Direction, OrderRequest, OrderType
        from anchor.futures.rebalance import build_futures_rebalance_plan

        if not settings.futures_v1_enabled:
            logger.info("futures_v1_rebalance_skipped", reason="disabled")
            return

        await init_db()
        import anchor.database.engine as _db_engine

        broker = BrokerClient()
        account = await broker.get_account_summary()
        equity = float(account.get("NAV", account.get("balance", 0.0)) or 0.0)
        live_margin_usage_pct = _account_margin_usage_pct(account)
        force_derisk = bool(
            live_margin_usage_pct is not None
            and live_margin_usage_pct >= settings.futures_margin_force_derisk_usage_pct
        )
        actual_positions = await broker.get_open_positions()
        pending_orders = await broker.get_pending_orders()
        force_derisk_position = _select_force_derisk_position(actual_positions) if force_derisk else None

        markets = list(settings.futures_v1_markets)
        plan = build_futures_rebalance_plan(
            data_dir="/app/data",
            equity=equity,
            actual_positions=actual_positions,
            pending_orders=pending_orders,
            markets=markets,
        )

        async with _db_engine.AsyncSessionFactory() as session:
            await _drawdown_monitor.bootstrap_peak_equity(session)
            await _drawdown_monitor.bootstrap_month_state(session, current_equity=equity)
            _drawdown_monitor.update(equity)
            drawdown_allowed, drawdown_reason = _drawdown_monitor.check()
            block_new_opens = bool(
                (live_margin_usage_pct is not None
                and live_margin_usage_pct >= settings.futures_margin_block_new_opens_pct)
                or not drawdown_allowed
                or force_derisk
            )

            event_repo = SystemEventRepository(session)
            execution_errors: list[dict[str, str]] = []
            blocked_open_actions: list[dict[str, str]] = []
            forced_derisk_action: dict[str, str] | None = None
            await event_repo.insert(
                event_type="FUTURES_V1_REBALANCE_PLAN",
                severity="INFO",
                component="FUTURES",
                message=f"Futures v1 rebalance plan generated with {len(plan.actions)} actions",
                metadata={
                    "as_of": plan.as_of,
                    "equity": plan.equity,
                    "auto_execute": settings.futures_v1_auto_execute,
                    "markets": markets,
                    "gross_notional_usd": round(plan.gross_notional_usd, 2),
                    "estimated_margin_usd": round(plan.estimated_margin_usd, 2),
                    "estimated_margin_usage_pct": round(plan.estimated_margin_usage_pct, 6),
                    "blocked_reason": plan.blocked_reason,
                    "live_margin_usage_pct": round(live_margin_usage_pct, 6) if live_margin_usage_pct is not None else None,
                    "drawdown_guard_allowed": drawdown_allowed,
                    "drawdown_guard_reason": drawdown_reason,
                    "force_derisk": force_derisk,
                    "force_derisk_instrument": str(force_derisk_position.get("instrument")) if force_derisk_position else None,
                    "block_new_opens": block_new_opens,
                    "execution_errors": execution_errors,
                    "desired_positions": [
                        {
                            "market": position.market,
                            "instrument": position.instrument,
                            "contracts": position.contracts,
                            "weight": round(position.weight, 6),
                            "reference_price": position.reference_price,
                            "notional_usd": round(position.notional_usd, 2),
                            "estimated_margin_usd": round(position.estimated_margin_usd, 2),
                        }
                        for position in plan.desired_positions
                    ],
                    "pending_orders": plan.pending_orders,
                    "actions": [
                        {
                            "action": action.action,
                            "market": action.market,
                            "instrument": action.instrument,
                            "contracts": action.contracts,
                            "direction": action.direction,
                            "reason": action.reason,
                        }
                        for action in plan.actions
                    ],
                },
            )

            if live_margin_usage_pct is not None and live_margin_usage_pct >= settings.futures_margin_block_new_opens_pct:
                await event_repo.insert(
                    event_type="FUTURES_MARGIN_GUARD",
                    severity="WARN",
                    component="RISK",
                    message=(
                        f"Blocked new futures opens at live margin usage {live_margin_usage_pct:.1%} "
                        f"(threshold {settings.futures_margin_block_new_opens_pct:.1%})"
                    ),
                    metadata={
                        "live_margin_usage_pct": live_margin_usage_pct,
                        "block_new_opens_threshold": settings.futures_margin_block_new_opens_pct,
                        "available_funds": float(account.get("availableFunds", 0.0) or 0.0),
                        "excess_liquidity": float(account.get("excessLiquidity", 0.0) or 0.0),
                        "maint_margin_req": float(account.get("maintMarginReq", 0.0) or 0.0),
                        "full_maint_margin_req": float(account.get("fullMaintMarginReq", 0.0) or 0.0),
                    },
                )

            if force_derisk and force_derisk_position is not None:
                units = abs(int(round(float(force_derisk_position.get("currentUnits", 0.0) or 0.0))))
                close_units = "1" if units >= 1 else "ALL"
                instrument = str(force_derisk_position.get("instrument", ""))
                forced_derisk_action = {
                    "instrument": instrument,
                    "units": close_units,
                    "reason": "margin_force_derisk",
                }
                await event_repo.insert(
                    event_type="FUTURES_MARGIN_GUARD",
                    severity="WARN",
                    component="RISK",
                    message=(
                        f"Force de-risking {instrument} by {close_units} contract at live margin usage "
                        f"{live_margin_usage_pct:.1%} (threshold {settings.futures_margin_force_derisk_usage_pct:.1%})"
                    ),
                    metadata={
                        "live_margin_usage_pct": live_margin_usage_pct,
                        "force_derisk_threshold": settings.futures_margin_force_derisk_usage_pct,
                        "instrument": instrument,
                        "estimated_margin_burden": _estimated_position_margin_burden(force_derisk_position),
                    },
                )
                await broker.close_trade(instrument, units=close_units)

            if not drawdown_allowed:
                await event_repo.insert(
                    event_type="FUTURES_DRAWDOWN_GUARD",
                    severity="WARN",
                    component="RISK",
                    message=f"Blocked new futures opens due to drawdown guard: {drawdown_reason}",
                    metadata={
                        "drawdown_reason": drawdown_reason,
                        "drawdown_pct": _drawdown_monitor.current_drawdown,
                        "scale_factor": _drawdown_monitor.scale_factor,
                    },
                )

            if settings.futures_v1_auto_execute and not plan.blocked_reason:
                for action in plan.actions:
                    try:
                        if action.action == "close":
                            await broker.close_trade(action.instrument, units="ALL")
                        elif action.action == "open":
                            if block_new_opens:
                                logger.warning(
                                    "futures_margin_guard_blocked_open",
                                    market=action.market,
                                    instrument=action.instrument,
                                    direction=action.direction,
                                    live_margin_usage_pct=live_margin_usage_pct,
                                )
                                blocked_open_actions.append(
                                    {
                                        "market": action.market,
                                        "instrument": action.instrument,
                                        "direction": action.direction,
                                        "reason": drawdown_reason or ("margin_force_derisk" if force_derisk else "margin_guard_block_new_opens"),
                                    }
                                )
                                continue
                            reference_price = next(
                                (position.reference_price for position in plan.desired_positions if position.instrument == action.instrument),
                                0.0,
                            )
                            stop_loss = _vol_based_emergency_stop(
                                data_dir="/app/data",
                                instrument=action.instrument,
                                direction=action.direction,
                                reference_price=float(reference_price or 0.0),
                                vol_lookback_days=settings.futures_vol_lookback_days,
                            )
                            request = OrderRequest(
                                instrument=action.instrument,
                                direction=Direction.LONG if action.direction == "LONG" else Direction.SHORT,
                                units=action.contracts,
                                order_type=OrderType.MARKET,
                                stop_loss=stop_loss,
                            )
                            await broker.place_order(uuid.uuid4(), request)
                    except Exception as exc:  # pragma: no cover - depends on live broker responses
                        logger.error(
                            "futures_v1_rebalance_action_failed",
                            market=action.market,
                            instrument=action.instrument,
                            action=action.action,
                            direction=action.direction,
                            error=str(exc),
                        )
                        execution_errors.append(
                            {
                                "market": action.market,
                                "instrument": action.instrument,
                                "action": action.action,
                                "direction": action.direction,
                                "error": str(exc),
                            }
                        )

            await session.commit()

        logger.info(
            "futures_v1_rebalance_complete",
            markets=markets,
            equity=equity,
            actions=len(plan.actions),
            blocked_reason=plan.blocked_reason,
            execution_errors=len(execution_errors),
            auto_execute=settings.futures_v1_auto_execute,
            live_margin_usage_pct=live_margin_usage_pct,
            drawdown_guard_allowed=drawdown_allowed,
            drawdown_guard_reason=drawdown_reason,
            force_derisk=force_derisk,
            force_derisk_instrument=forced_derisk_action["instrument"] if forced_derisk_action else None,
            block_new_opens=block_new_opens,
            blocked_open_actions=len(blocked_open_actions),
        )

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("futures_v1_rebalance_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=120)
