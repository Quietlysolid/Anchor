"""Celery application configuration."""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from anchor.config import settings

def _futures_rebalance_schedule():
    hour, minute = [int(part) for part in settings.futures_daily_signal_time_utc.split(":", 1)]
    frequency = settings.futures_rebalance_frequency.strip().lower()
    if frequency == "daily":
        return crontab(hour=hour, minute=minute, day_of_week="1-5")
    if frequency == "weekly":
        return crontab(hour=hour, minute=minute, day_of_week="1")
    raise ValueError(f"Unsupported futures_rebalance_frequency: {settings.futures_rebalance_frequency}")

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
        "anchor.scheduler.jobs.snapshot_equity": {"queue": "default"},
        "anchor.scheduler.jobs.reconcile_positions": {"queue": "default"},
        "anchor.scheduler.jobs.run_futures_v1_rebalance": {"queue": "default"},
        "anchor.scheduler.jobs.startup_diagnostics": {"queue": "default"},
        "anchor.scheduler.jobs.refresh_futures_data": {"queue": "default"},
    },
    # Beat schedule (periodic tasks)
    beat_schedule={
        "snapshot-equity-every-15-min": {
            "task": "anchor.scheduler.jobs.snapshot_equity",
            "schedule": 900.0,  # 15 minutes
        },
        "reconcile-positions-every-15-min": {
            "task": "anchor.scheduler.jobs.reconcile_positions",
            "schedule": 900.0,
        },
        "futures-v1-rebalance-weekdays": {
            "task": "anchor.scheduler.jobs.run_futures_v1_rebalance",
            "schedule": _futures_rebalance_schedule(),
        },
        "startup-diagnostics-daily": {
            "task": "anchor.scheduler.jobs.startup_diagnostics",
            "schedule": 86_400.0,
        },
        "refresh-futures-data-daily": {
            "task": "anchor.scheduler.jobs.refresh_futures_data",
            "schedule": crontab(hour=21, minute=0, day_of_week="1-5"),
        },
    },
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)


@celery_app.on_after_finalize.connect
def _schedule_startup_diagnostics(sender, **kwargs):
    """Prime critical diagnostics and external-data caches on worker startup."""
    sender.send_task("anchor.scheduler.jobs.startup_diagnostics")
