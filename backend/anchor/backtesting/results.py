"""Backtesting results calculator and reporter."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np

from anchor.backtesting.simulated_broker import SimulatedBroker, SimulatedPosition


@dataclass
class BacktestResults:
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
    trade_log: List[dict] = field(default_factory=list)


def compute_results(broker: SimulatedBroker, instrument: str, timeframe: str) -> BacktestResults:
    trades = broker.trade_history
    if not trades:
        return BacktestResults(
            instrument=instrument,
            timeframe=timeframe,
            start_date="",
            end_date="",
            initial_balance=broker.account_balance,
            final_balance=broker.account_balance,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0.0,
            profit_factor=0.0,
            net_pnl=0.0,
            net_pnl_pct=0.0,
            max_drawdown_pct=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            avg_win_pips=0.0,
            avg_loss_pips=0.0,
            avg_trade_duration_hours=0.0,
            largest_win=0.0,
            largest_loss=0.0,
            consecutive_wins=0,
            consecutive_losses=0,
        )

    pnls = [t.net_pl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    initial_balance = broker.equity_history[0]["balance"] if broker.equity_history else broker.account_balance

    # Drawdown from equity curve
    if broker.equity_history:
        equity = np.array([e["equity"] for e in broker.equity_history])
        peak = np.maximum.accumulate(equity)
        drawdowns = (peak - equity) / peak
        max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0
    else:
        max_dd = 0.0

    # Sharpe (annualized, assume daily returns from equity curve)
    if len(broker.equity_history) > 1:
        eq = np.array([e["equity"] for e in broker.equity_history])
        daily_returns = np.diff(eq) / eq[:-1]
        if daily_returns.std() > 0:
            sharpe = float(daily_returns.mean() / daily_returns.std() * np.sqrt(252))
        else:
            sharpe = 0.0
        neg = daily_returns[daily_returns < 0]
        if neg.std() > 0:
            sortino = float(daily_returns.mean() / neg.std() * np.sqrt(252))
        else:
            sortino = 0.0
    else:
        sharpe = sortino = 0.0

    # Duration
    durations = []
    for t in trades:
        if t.exit_time and t.entry_time:
            durations.append((t.exit_time - t.entry_time).total_seconds() / 3600)

    # Consecutive runs
    max_cons_wins = max_cons_losses = 0
    cur_wins = cur_losses = 0
    for p in pnls:
        if p > 0:
            cur_wins += 1
            cur_losses = 0
        else:
            cur_losses += 1
            cur_wins = 0
        max_cons_wins = max(max_cons_wins, cur_wins)
        max_cons_losses = max(max_cons_losses, cur_losses)

    profit_factor = sum(wins) / abs(sum(losses)) if losses else float("inf")

    return BacktestResults(
        instrument=instrument,
        timeframe=timeframe,
        start_date=str(trades[0].entry_time.date()),
        end_date=str(trades[-1].exit_time.date() if trades[-1].exit_time else ""),
        initial_balance=round(initial_balance, 2),
        final_balance=round(broker.account_balance, 2),
        total_trades=len(trades),
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=round(len(wins) / len(trades), 4),
        profit_factor=round(profit_factor, 2),
        net_pnl=round(sum(pnls), 2),
        net_pnl_pct=round(sum(pnls) / initial_balance * 100, 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        sharpe_ratio=round(sharpe, 2),
        sortino_ratio=round(sortino, 2),
        avg_win_pips=round(np.mean(wins), 2) if wins else 0.0,
        avg_loss_pips=round(np.mean(losses), 2) if losses else 0.0,
        avg_trade_duration_hours=round(np.mean(durations), 1) if durations else 0.0,
        largest_win=round(max(wins), 2) if wins else 0.0,
        largest_loss=round(min(losses), 2) if losses else 0.0,
        consecutive_wins=max_cons_wins,
        consecutive_losses=max_cons_losses,
        trade_log=[
            {
                "id": t.id,
                "instrument": t.instrument,
                "direction": t.direction,
                "units": t.units,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "entry_time": str(t.entry_time),
                "exit_time": str(t.exit_time),
                "net_pl": round(t.net_pl, 2),
                "close_reason": t.close_reason,
            }
            for t in trades
        ],
    )
