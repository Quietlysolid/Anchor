"""
Monte Carlo simulation.
10,000 random permutations of the historical trade PnL sequence.
Extracts distribution of max drawdowns, risk of ruin, and return percentiles.

Starting capital must be passed explicitly (from account balance at start of
sample window) rather than derived from the PnL series, which would produce
incorrect drawdown percentages.
"""
from dataclasses import dataclass

import numpy as np


@dataclass
class MonteCarloResult:
    n_simulations:       int
    median_return:       float
    p5_return:           float
    p95_return:          float
    median_max_drawdown: float
    p95_max_drawdown:    float
    risk_of_ruin:        float     # probability of losing ≥ ruin_threshold_pct of starting capital
    expected_sharpe:     float


def run_monte_carlo(
    pnl_series:          list[float],
    starting_capital:    float = 10_000.0,
    n_simulations:       int   = 10_000,
    ruin_threshold_pct:  float = 0.50,
) -> MonteCarloResult:
    """
    pnl_series:        list of per-trade PnL values (dollar amounts).
    starting_capital:  account balance at the start of the sample window.
                       Used as denominator for return/drawdown percentages.
    """
    if not pnl_series:
        return MonteCarloResult(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    arr = np.array(pnl_series, dtype=float)
    capital = max(starting_capital, 1.0)  # guard against zero

    all_final_returns: list[float] = []
    all_max_drawdowns: list[float] = []
    ruin_count = 0

    rng = np.random.default_rng()  # non-deterministic seed for production use

    ruin_floor = -capital * ruin_threshold_pct

    for _ in range(n_simulations):
        shuffled     = rng.permutation(arr)
        equity_curve = capital + np.cumsum(shuffled)

        # Max drawdown: use running peak (correct drawdown formula)
        peak   = np.maximum.accumulate(equity_curve)
        dd_abs = equity_curve - peak           # always ≤ 0
        max_dd = float(np.min(dd_abs)) / capital  # as fraction of starting capital
        all_max_drawdowns.append(abs(max_dd))

        final_return = (float(equity_curve[-1]) - capital) / capital
        all_final_returns.append(final_return)

        if float(np.min(equity_curve)) < capital + ruin_floor:
            ruin_count += 1

    returns_arr = np.array(all_final_returns)
    dd_arr      = np.array(all_max_drawdowns)

    return MonteCarloResult(
        n_simulations       = n_simulations,
        median_return       = round(float(np.median(returns_arr)), 4),
        p5_return           = round(float(np.percentile(returns_arr, 5)),  4),
        p95_return          = round(float(np.percentile(returns_arr, 95)), 4),
        median_max_drawdown = round(float(np.median(dd_arr)),              4),
        p95_max_drawdown    = round(float(np.percentile(dd_arr, 95)),      4),
        risk_of_ruin        = round(ruin_count / n_simulations,            4),
        expected_sharpe     = round(
            float(np.mean(returns_arr)) / (float(np.std(returns_arr)) + 1e-10), 4
        ),
    )
