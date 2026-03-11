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
        "anchor.scheduler.jobs.update_fred_rates": {"queue": "default"},
        "anchor.scheduler.jobs.reconcile_positions": {"queue": "default"},
        "anchor.scheduler.jobs.run_signal_scan": {"queue": "default"},
        "anchor.scheduler.jobs.run_regime_detection": {"queue": "default"},
        "anchor.scheduler.jobs.import_candles": {"queue": "default"},
        "anchor.scheduler.jobs.close_stale_trades": {"queue": "default"},
        "anchor.scheduler.jobs.update_vix": {"queue": "default"},
        "anchor.scheduler.jobs.update_oanda_sentiment": {"queue": "default"},
        "anchor.scheduler.jobs.startup_diagnostics": {"queue": "default"},
        "anchor.scheduler.jobs.run_partial_tp": {"queue": "default"},
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
        "update-fred-rates-daily": {
            "task": "anchor.scheduler.jobs.update_fred_rates",
            "schedule": 86_400.0,  # 24 hours
        },
        "reconcile-positions-every-15-min": {
            "task": "anchor.scheduler.jobs.reconcile_positions",
            "schedule": 900.0,
        },
        "retrain-models-monthly": {
            "task": "anchor.scheduler.jobs.retrain_models",
            "schedule": 2_592_000.0,  # ~30 days
        },
        "signal-scan-every-5-min": {
            "task": "anchor.scheduler.jobs.run_signal_scan",
            "schedule": 300.0,  # every 5 minutes for real-time responsiveness
        },
        "regime-detection-every-hour": {
            "task": "anchor.scheduler.jobs.run_regime_detection",
            "schedule": 3_600.0,
        },
        "import-candles-every-hour": {
            "task": "anchor.scheduler.jobs.import_candles",
            "schedule": 3_600.0,  # every hour, 5 min before regime detection
        },
        "close-stale-trades-every-hour": {
            "task": "anchor.scheduler.jobs.close_stale_trades",
            "schedule": 3_600.0,  # hourly check — 12h threshold means no urgency
        },
        "update-vix-every-4-hours": {
            "task": "anchor.scheduler.jobs.update_vix",
            "schedule": 14_400.0,  # 4 hours — VIX is daily, refresh 6x/day is plenty
        },
        "update-oanda-sentiment-every-5-min": {
            "task": "anchor.scheduler.jobs.update_oanda_sentiment",
            "schedule": 300.0,  # 5 min — aligns with signal scan cadence
        },
        "startup-diagnostics-daily": {
            "task": "anchor.scheduler.jobs.startup_diagnostics",
            "schedule": 86_400.0,  # runs once at startup then every 24h
        },
        "partial-tp-every-5-min": {
            "task": "anchor.scheduler.jobs.run_partial_tp",
            "schedule": 300.0,  # aligns with signal scan — checks open positions every 5 min
        },
    },
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)


@celery_app.on_after_finalize.connect
def _schedule_startup_diagnostics(sender, **kwargs):
    """Fire startup_diagnostics immediately when the worker process is ready."""
    sender.send_task("anchor.scheduler.jobs.startup_diagnostics")
