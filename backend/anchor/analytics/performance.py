"""
Performance analytics: Sharpe, Sortino, Calmar, win rate, profit factor.
"""
from dataclasses import dataclass

import numpy as np

from anchor.utils.math_utils import (
    sharpe_ratio, sortino_ratio, calmar_ratio,
    profit_factor, max_drawdown,
)


@dataclass
class PerformanceSummary:
    total_trades:    int
    win_rate:        float
    profit_factor:   float
    sharpe_ratio:    float
    sortino_ratio:   float
    calmar_ratio:    float
    max_drawdown:    float
    avg_win_pips:    float
    avg_loss_pips:   float
    net_pnl:         float
    gross_pnl:       float
    total_commission: float


def compute_performance(trades: list, initial_balance: float = 10_000.0) -> PerformanceSummary:
    """
    trades: list of Trade ORM objects with .net_pl, .gross_pl, .commission fields.
    initial_balance: starting account equity for drawdown calculation.
    """
    if not trades:
        return PerformanceSummary(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    # Sort by open time so cumulative equity is correct
    sorted_trades = sorted(trades, key=lambda t: t.opened_at)
    pnl_array  = np.array([float(t.net_pl) for t in sorted_trades])

    wins   = pnl_array[pnl_array > 0]
    losses = pnl_array[pnl_array < 0]

    win_rate      = len(wins) / len(sorted_trades) if sorted_trades else 0.0
    pf            = profit_factor(pnl_array)
    net_pnl       = float(np.sum(pnl_array))
    gross_pnl     = float(sum(float(t.gross_pl) for t in sorted_trades))
    total_comm    = float(sum(float(t.commission) for t in sorted_trades))

    # Build equity curve from initial balance so drawdown is meaningful
    equity_curve  = initial_balance + np.cumsum(pnl_array)
    mdd           = max_drawdown(equity_curve) if len(equity_curve) > 1 else 0.0

    return PerformanceSummary(
        total_trades    = len(trades),
        win_rate        = round(win_rate, 4),
        profit_factor   = round(pf, 4),
        sharpe_ratio    = round(sharpe_ratio(pnl_array), 4),
        sortino_ratio   = round(sortino_ratio(pnl_array), 4),
        calmar_ratio    = round(calmar_ratio(pnl_array), 4),
        max_drawdown    = round(mdd, 4),
        avg_win_pips    = round(float(np.mean(wins)),   2) if len(wins)   > 0 else 0.0,
        avg_loss_pips   = round(float(np.mean(losses)), 2) if len(losses) > 0 else 0.0,
        net_pnl         = round(net_pnl,    2),
        gross_pnl       = round(gross_pnl,  2),
        total_commission = round(total_comm, 2),
    )
