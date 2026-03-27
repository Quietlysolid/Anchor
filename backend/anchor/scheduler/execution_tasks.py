"""Execution tasks for the futures runtime."""
from __future__ import annotations

import structlog

from anchor.scheduler.celery_app import celery_app
from anchor.scheduler._shared import _run_async, _daily_limiter

logger = structlog.get_logger(__name__)


@celery_app.task(name="anchor.scheduler.jobs.reconcile_positions", bind=True, max_retries=3)
def reconcile_positions(self):
    """Reconcile DB positions vs broker ground truth every 15 minutes."""
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
