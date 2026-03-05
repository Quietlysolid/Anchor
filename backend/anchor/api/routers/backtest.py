"""Backtest API endpoint — triggers a historical backtest from the dashboard."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/backtest", tags=["backtest"])


class BacktestRequest(BaseModel):
    instrument: str = "EUR_USD"
    start_date: str = "2020-01-01"
    end_date: Optional[str] = None
    initial_balance: float = 10_000.0


class BacktestResponse(BaseModel):
    instrument: str
    timeframe: str
    start_date: str
    end_date: str
    initial_balance: float
    final_balance: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    net_pnl: float
    net_pnl_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    avg_win_pips: float
    avg_loss_pips: float
    avg_trade_duration_hours: float
    largest_win: float
    largest_loss: float
    consecutive_wins: int
    consecutive_losses: int


@router.post("/run", response_model=BacktestResponse)
async def run_backtest(req: BacktestRequest):
    """
    Run a historical backtest using stored candle data.
    Fetches data from DB and runs the full confluence engine.
    """
    from anchor.database.engine import get_session
    from anchor.database.repositories import MarketDataRepository
    from anchor.backtesting.engine import BacktestEngine

    import pandas as pd

    start = datetime.strptime(req.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = (
        datetime.strptime(req.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if req.end_date
        else datetime.now(timezone.utc)
    )

    async with get_session() as session:
        repo = MarketDataRepository(session)

        # Load H1 candles
        h1_candles = await repo.get_candles(req.instrument, "H1", start, end, limit=50_000)
        h4_candles = await repo.get_candles(req.instrument, "H4", start, end, limit=15_000)
        d1_candles = await repo.get_candles(req.instrument, "D", start, end, limit=2_000)

    if len(h1_candles) < 100:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient H1 data for {req.instrument} ({len(h1_candles)} bars). "
                   "Run data import first.",
        )

    def candles_to_df(candles):
        return pd.DataFrame([
            {
                "time": c.time,
                "open": c.open, "high": c.high, "low": c.low,
                "close": c.close, "volume": c.volume or 0,
            }
            for c in candles
        ])

    eng = BacktestEngine(initial_balance=req.initial_balance)
    eng.load_df(req.instrument, "H1", candles_to_df(h1_candles))
    if h4_candles:
        eng.load_df(req.instrument, "H4", candles_to_df(h4_candles))
    if d1_candles:
        eng.load_df(req.instrument, "D", candles_to_df(d1_candles))

    results = eng.run(req.instrument, "H1")

    return BacktestResponse(
        instrument=results.instrument,
        timeframe=results.timeframe,
        start_date=results.start_date,
        end_date=results.end_date,
        initial_balance=results.initial_balance,
        final_balance=results.final_balance,
        total_trades=results.total_trades,
        winning_trades=results.winning_trades,
        losing_trades=results.losing_trades,
        win_rate=results.win_rate,
        profit_factor=results.profit_factor,
        net_pnl=results.net_pnl,
        net_pnl_pct=results.net_pnl_pct,
        max_drawdown_pct=results.max_drawdown_pct,
        sharpe_ratio=results.sharpe_ratio,
        sortino_ratio=results.sortino_ratio,
        avg_win_pips=results.avg_win_pips,
        avg_loss_pips=results.avg_loss_pips,
        avg_trade_duration_hours=results.avg_trade_duration_hours,
        largest_win=results.largest_win,
        largest_loss=results.largest_loss,
        consecutive_wins=results.consecutive_wins,
        consecutive_losses=results.consecutive_losses,
    )
