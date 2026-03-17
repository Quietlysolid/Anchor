"""Shared utilities and process-level singletons for Celery task modules.

These are imported by each task module. Because Python caches module imports,
all task modules share the exact same singleton instances within one worker process.
DrawdownMonitor and DailyLimiter MUST be singletons — their internal state
(_peak_equity, daily P&L) must accumulate across task invocations.
"""
from __future__ import annotations

import asyncio

import structlog

from anchor.risk.drawdown_monitor import DrawdownMonitor as _DrawdownMonitor
from anchor.risk.daily_limiter import DailyLimiter as _DailyLimiter
from anchor.risk.spread_monitor import SpreadMonitor as _SpreadMonitor
from anchor.monitoring.alerts import AlertService as _AlertService

logger = structlog.get_logger(__name__)

_drawdown_monitor = _DrawdownMonitor()
_daily_limiter = _DailyLimiter()
_spread_monitor = _SpreadMonitor()
_alerts = _AlertService()


def _run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
