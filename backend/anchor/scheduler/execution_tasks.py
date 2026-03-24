"""Execution tasks: position reconciliation, partial TP, stale trade closure."""
from __future__ import annotations

from datetime import timedelta

import structlog

from anchor.scheduler.celery_app import celery_app
from anchor.scheduler._shared import _run_async, _daily_limiter, _alerts

logger = structlog.get_logger(__name__)


@celery_app.task(name="anchor.scheduler.jobs.reconcile_positions", bind=True, max_retries=3)
def reconcile_positions(self):
    """Reconcile DB positions vs OANDA ground truth every 15 minutes."""
    async def _inner():
        from anchor.database.engine import init_db
        from anchor.database.repositories.positions import PositionRepository
        from anchor.database.repositories.orders import OrderRepository
        from anchor.database.repositories.events import SystemEventRepository
        from anchor.execution.broker_client import BrokerClient
        from anchor.execution.reconciler import Reconciler

        await init_db()
        import anchor.database.engine as _db_engine

        client = BrokerClient()
        async with _db_engine.AsyncSessionFactory() as session:
            reconciler = Reconciler(
                broker_client=client,
                position_repo=PositionRepository(session),
                order_repo=OrderRepository(session),
                system_event_repo=SystemEventRepository(session),
                daily_limiter=_daily_limiter,
            )
            report = await reconciler.reconcile()
            await session.commit()
        actions = len(report.get("actions_taken", []))
        if actions:
            logger.info("reconciliation_complete", actions=actions, report=report)

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("reconcile_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="anchor.scheduler.jobs.close_stale_trades", bind=True, max_retries=2)
def close_stale_trades(self):
    """Close open trades that have not reached 50% of TP distance within 12 hours.

    A trade drifting sideways for 12 hours has failed its thesis. Holding it
    ties up capital and a correlation slot. Close it at market to free both.
    """
    async def _inner():
        from anchor.database.engine import init_db
        from anchor.database.repositories.positions import PositionRepository
        from anchor.execution.broker_client import BrokerClient
        from anchor.utils.time_utils import utcnow

        await init_db()
        import anchor.database.engine as _db_engine

        broker = BrokerClient()
        now = utcnow()
        stale_threshold = timedelta(hours=12)

        async with _db_engine.AsyncSessionFactory() as session:
            repo = PositionRepository(session)
            open_positions = await repo.get_open()

            for pos in open_positions:
                age = now - pos.opened_at
                if age < stale_threshold:
                    continue

                if pos.take_profit is None or pos.stop_loss is None:
                    continue

                entry = float(pos.avg_entry_price)
                tp    = float(pos.take_profit)
                price = float(pos.current_price)
                tp_distance = abs(tp - entry)

                if pos.direction == "LONG":
                    progress = (price - entry) / tp_distance if tp_distance > 0 else 0
                else:
                    progress = (entry - price) / tp_distance if tp_distance > 0 else 0

                if progress < 0.5:
                    logger.info(
                        "closing_stale_trade",
                        instrument=pos.instrument,
                        direction=pos.direction,
                        age_hours=round(age.total_seconds() / 3600, 1),
                        tp_progress=round(progress, 2),
                        oanda_trade_id=pos.oanda_trade_id,
                    )
                    try:
                        await broker.close_trade(pos.oanda_trade_id)
                        await repo.mark_closed(pos.id, "STALE_12H", now, exit_price=price)
                    except Exception as exc:
                        logger.error("stale_close_failed", trade_id=pos.oanda_trade_id, error=str(exc))

            await session.commit()

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("close_stale_trades_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="anchor.scheduler.jobs.run_partial_tp", bind=True, max_retries=2)
def run_partial_tp(self):
    """Check all open positions and execute partial TP where the 1×ATR trigger has been hit.

    Runs every 5 minutes. At 1×ATR profit:
      - Close 50% of position at market
      - Move SL to breakeven on remaining 50%
      - Let remainder run to original TP
    """
    async def _inner():
        from anchor.database.engine import init_db
        from anchor.database.repositories.positions import PositionRepository
        from anchor.execution.broker_client import BrokerClient
        from anchor.execution.partial_tp_manager import PartialTPManager

        await init_db()
        import anchor.database.engine as _db_engine

        broker = BrokerClient()

        async with _db_engine.AsyncSessionFactory() as session:
            pos_repo = PositionRepository(session)
            manager  = PartialTPManager(
                broker_client=broker,
                position_repo=pos_repo,
                alerts=_alerts,
            )
            executed = await manager.run()
            await session.commit()

        if executed:
            logger.info("partial_tp_cycle_complete", partial_closes=executed)

    try:
        _run_async(_inner())
    except Exception as exc:
        logger.error("run_partial_tp_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)
