"""Celery application configuration."""
from __future__ import annotations

from celery import Celery

from anchor.config import settings

celery_app = Celery(
    "anchor",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["anchor.scheduler.jobs"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Task routing
    task_routes={
        "anchor.scheduler.jobs.retrain_models": {"queue": "ml_tasks"},
        "anchor.scheduler.jobs.import_economic_calendar": {"queue": "default"},
        "anchor.scheduler.jobs.snapshot_equity": {"queue": "default"},
        "anchor.scheduler.jobs.update_cot_data": {"queue": "default"},
        "anchor.scheduler.jobs.reconcile_positions": {"queue": "default"},
    },
    # Beat schedule (periodic tasks)
    beat_schedule={
        "snapshot-equity-every-15-min": {
            "task": "anchor.scheduler.jobs.snapshot_equity",
            "schedule": 900.0,  # 15 minutes
        },
        "import-calendar-daily": {
            "task": "anchor.scheduler.jobs.import_economic_calendar",
            "schedule": 86_400.0,  # 24 hours
        },
        "update-cot-weekly": {
            "task": "anchor.scheduler.jobs.update_cot_data",
            "schedule": 604_800.0,  # 7 days
        },
        "reconcile-positions-every-15-min": {
            "task": "anchor.scheduler.jobs.reconcile_positions",
            "schedule": 900.0,
        },
        "retrain-models-monthly": {
            "task": "anchor.scheduler.jobs.retrain_models",
            "schedule": 2_592_000.0,  # ~30 days
        },
    },
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)
