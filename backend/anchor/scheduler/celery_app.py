"""Celery application configuration."""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

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
        "anchor.scheduler.jobs.update_order_book": {"queue": "default"},
        "anchor.scheduler.jobs.update_cme_flow": {"queue": "default"},
        "anchor.scheduler.jobs.update_fx_options": {"queue": "default"},
        "anchor.scheduler.jobs.update_cross_asset": {"queue": "default"},
        "anchor.scheduler.jobs.startup_diagnostics": {"queue": "default"},
        "anchor.scheduler.jobs.run_partial_tp": {"queue": "default"},
        "anchor.scheduler.jobs.check_fit_weights_trigger":  {"queue": "default"},
        "anchor.scheduler.jobs.monitor_live_performance":   {"queue": "default"},
        "anchor.scheduler.jobs.assess_edge_confidence":       {"queue": "default"},
        "anchor.scheduler.jobs.generate_presession_brief":    {"queue": "default"},
        "anchor.scheduler.jobs.generate_postsession_debrief": {"queue": "default"},
        "anchor.scheduler.jobs.generate_weekly_synthesis":    {"queue": "default"},
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
        "update-order-book-every-5-min": {
            "task": "anchor.scheduler.jobs.update_order_book",
            "schedule": 300.0,  # 5 min — aligns with signal scan + sentiment
        },
        "update-cme-flow-every-2-hours": {
            "task": "anchor.scheduler.jobs.update_cme_flow",
            "schedule": 7_200.0,  # 2 hours — daily futures data, no need for more
        },
        "update-fx-options-every-4-hours": {
            "task": "anchor.scheduler.jobs.update_fx_options",
            "schedule": 14_400.0,  # 4 hours — options IV moves slowly intraday
        },
        "update-cross-asset-every-2-hours": {
            "task": "anchor.scheduler.jobs.update_cross_asset",
            "schedule": 7_200.0,   # 2 hours — daily SPY/GLD data, aligns with CME refresh
        },
        "startup-diagnostics-daily": {
            "task": "anchor.scheduler.jobs.startup_diagnostics",
            "schedule": 86_400.0,  # runs once at startup then every 24h
        },
        "partial-tp-every-5-min": {
            "task": "anchor.scheduler.jobs.run_partial_tp",
            "schedule": 300.0,  # aligns with signal scan — checks open positions every 5 min
        },
        "check-fit-weights-trigger-daily": {
            "task": "anchor.scheduler.jobs.check_fit_weights_trigger",
            "schedule": 86_400.0,  # daily — fires at 200 trades then every 200 after
        },
        "monitor-live-performance-daily": {
            "task": "anchor.scheduler.jobs.monitor_live_performance",
            "schedule": 86_400.0,  # daily — compares rolling WR/PF vs backtest benchmarks
        },
        "assess-edge-confidence-daily": {
            "task": "anchor.scheduler.jobs.assess_edge_confidence",
            "schedule": 86_400.0,  # daily after London close — detects macro dislocation
        },
        "update-economic-surprise-every-30-min": {
            "task": "anchor.scheduler.jobs.update_economic_surprise",
            "schedule": 1_800.0,  # 30 min — cheap DB read, only meaningful after releases
        },
        # ── AI Intelligence Layer ────────────────────────────────────────────
        "generate-trade-explanations-every-30-min": {
            "task": "anchor.scheduler.jobs.generate_trade_explanations",
            "schedule": 1_800.0,  # every 30 min — catches trades from last London or LCR session
        },
        "analyze-journal-patterns-weekly": {
            "task": "anchor.scheduler.jobs.analyze_journal_patterns",
            "schedule": crontab(hour=21, minute=0, day_of_week=0),  # Sunday 21:00 UTC
        },
        "intrabar-anomaly-check-london-hourly": {
            "task": "anchor.scheduler.jobs.run_intrabar_anomaly_check",
            "schedule": crontab(hour="7-11", minute=5, day_of_week="1-5"),  # 07:05–11:05 UTC Mon–Fri
        },
        "presession-brief-weekdays": {
            "task": "anchor.scheduler.jobs.generate_presession_brief",
            "schedule": crontab(hour=6, minute=30, day_of_week="1-5"),  # Mon–Fri 06:30 UTC
        },
        "postsession-debrief-weekdays": {
            "task": "anchor.scheduler.jobs.generate_postsession_debrief",
            "schedule": crontab(hour=12, minute=30, day_of_week="1-5"),  # Mon–Fri 12:30 UTC
        },
        "weekly-synthesis-sunday": {
            "task": "anchor.scheduler.jobs.generate_weekly_synthesis",
            "schedule": crontab(hour=22, minute=0, day_of_week=0),  # Sunday 22:00 UTC
        },
    },
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)


@celery_app.on_after_finalize.connect
def _schedule_startup_diagnostics(sender, **kwargs):
    """Fire startup_diagnostics immediately when the worker process is ready."""
    sender.send_task("anchor.scheduler.jobs.startup_diagnostics")
