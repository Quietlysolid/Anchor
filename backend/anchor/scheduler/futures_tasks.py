"""Futures-specific scheduled tasks."""
from __future__ import annotations

import uuid

import structlog

from anchor.scheduler.celery_app import celery_app
from anchor.scheduler._shared import _run_async

logger = structlog.get_logger(__name__)


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
        actual_positions = await broker.get_open_positions()
        pending_orders = await broker.get_pending_orders()

        markets = list(settings.futures_v1_markets)
        plan = build_futures_rebalance_plan(
            data_dir="/app/data",
            equity=equity,
            actual_positions=actual_positions,
            pending_orders=pending_orders,
            markets=markets,
        )

        async with _db_engine.AsyncSessionFactory() as session:
            event_repo = SystemEventRepository(session)
            execution_errors: list[dict[str, str]] = []
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

            if settings.futures_v1_auto_execute and not plan.blocked_reason:
                for action in plan.actions:
                    try:
                        if action.action == "close":
                            await broker.close_trade(action.instrument, units="ALL")
                        elif action.action == "open":
                            request = OrderRequest(
                                instrument=action.instrument,
                                direction=Direction.LONG if action.direction == "LONG" else Direction.SHORT,
                                units=action.contracts,
                                order_type=OrderType.MARKET,
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
        )

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("futures_v1_rebalance_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=120)
