"""Backtest tasks: on-demand historical simulation via API."""
from __future__ import annotations

from datetime import datetime, timezone

import structlog

from anchor.scheduler.celery_app import celery_app
from anchor.scheduler._shared import _run_async

logger = structlog.get_logger(__name__)


@celery_app.task(name="anchor.scheduler.jobs.run_backtest", bind=True)
def run_backtest(self, instrument: str, start_date: str, end_date: str, initial_balance: float):
    """Run a historical backtest and return the results dict.

    Called by POST /backtest/submit.  Runs in a Celery worker so the HTTP
    request returns immediately with a job_id.  Results are stored in the
    Celery/Redis result backend and fetched by GET /backtest/status/{job_id}.
    """
    import pandas as pd

    async def _fetch_candles():
        from anchor.database.engine import init_db, get_session
        from anchor.database.repositories import MarketDataRepository

        await init_db()

        start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end   = datetime.strptime(end_date,   "%Y-%m-%d").replace(tzinfo=timezone.utc)

        async with get_session() as session:
            repo = MarketDataRepository(session)
            h1 = await repo.get_candles(instrument, "H1", start, end, limit=50_000)
            h4 = await repo.get_candles(instrument, "H4", start, end, limit=15_000)
            d1 = await repo.get_candles(instrument, "D",  start, end, limit=2_000)
        return h1, h4, d1

    h1_candles, h4_candles, d1_candles = _run_async(_fetch_candles())

    if len(h1_candles) < 100:
        raise ValueError(
            f"Insufficient H1 data for {instrument} ({len(h1_candles)} bars). "
            "Run data import first."
        )

    def _to_df(candles):
        return pd.DataFrame([
            {"time": c.time,
             "open": float(c.open), "high": float(c.high),
             "low": float(c.low),   "close": float(c.close),
             "volume": float(c.volume or 0)}
            for c in candles
        ])

    from anchor.backtesting.engine import BacktestEngine
    eng = BacktestEngine(initial_balance=initial_balance)
    eng.load_df(instrument, "H1", _to_df(h1_candles))
    if h4_candles:
        eng.load_df(instrument, "H4", _to_df(h4_candles))
    if d1_candles:
        eng.load_df(instrument, "D", _to_df(d1_candles))

    r = eng.run(instrument, "H1")

    return {
        "instrument":               r.instrument,
        "timeframe":                r.timeframe,
        "start_date":               r.start_date,
        "end_date":                 r.end_date,
        "initial_balance":          r.initial_balance,
        "final_balance":            r.final_balance,
        "total_trades":             r.total_trades,
        "winning_trades":           r.winning_trades,
        "losing_trades":            r.losing_trades,
        "win_rate":                 r.win_rate,
        "profit_factor":            r.profit_factor,
        "net_pnl":                  r.net_pnl,
        "net_pnl_pct":              r.net_pnl_pct,
        "max_drawdown_pct":         r.max_drawdown_pct,
        "sharpe_ratio":             r.sharpe_ratio,
        "sortino_ratio":            r.sortino_ratio,
        "avg_win_pips":             r.avg_win_pips,
        "avg_loss_pips":            r.avg_loss_pips,
        "avg_trade_duration_hours": r.avg_trade_duration_hours,
        "largest_win":              r.largest_win,
        "largest_loss":             r.largest_loss,
        "consecutive_wins":         r.consecutive_wins,
        "consecutive_losses":       r.consecutive_losses,
    }
