"""Task registry — imports all task modules so Celery discovers them.

This file exists for backward compatibility. All task logic has been
split into domain-specific modules:

  snapshot_tasks.py    — equity snapshots, regime detection, startup diagnostics
  data_tasks.py        — candles, FRED, COT, VIX, sentiment, order book, CME, FX options, cross-asset
  signal_tasks.py      — London trend, M15, mean-reversion, LCR signal evaluation + execution
  execution_tasks.py   — position reconciliation, partial TP, stale trade closure
  monitoring_tasks.py  — live performance check, edge confidence, fit-weights trigger
  intelligence_tasks.py — AI briefs, anomaly checks, trade explanations, journal analysis
  ml_tasks.py          — ML model retraining
  backtest_tasks.py    — on-demand historical backtests
"""
from anchor.scheduler.snapshot_tasks import *      # noqa: F401, F403
from anchor.scheduler.data_tasks import *          # noqa: F401, F403
from anchor.scheduler.signal_tasks import *        # noqa: F401, F403
from anchor.scheduler.execution_tasks import *     # noqa: F401, F403
from anchor.scheduler.monitoring_tasks import *    # noqa: F401, F403
from anchor.scheduler.intelligence_tasks import *  # noqa: F401, F403
from anchor.scheduler.ml_tasks import *            # noqa: F401, F403
from anchor.scheduler.backtest_tasks import *      # noqa: F401, F403
