"""ML tasks: model retraining."""
from __future__ import annotations

from typing import Optional

import structlog

from anchor.scheduler.celery_app import celery_app
from anchor.scheduler._shared import _run_async

logger = structlog.get_logger(__name__)


@celery_app.task(name="anchor.scheduler.jobs.retrain_models", bind=True, max_retries=1)
def retrain_models(self, instrument: Optional[str] = None):
    """Monthly ML retraining for all instruments."""
    async def _inner():
        from anchor.ml.retraining import run_retraining
        results = await run_retraining(instrument)
        logger.info("retraining_complete", results=results)
        return results

    try:
        return _run_async(_inner())
    except Exception as exc:
        logger.error("retraining_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=3600)
