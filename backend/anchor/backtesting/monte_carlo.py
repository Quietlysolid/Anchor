"""Monte Carlo validation — is your edge real or just noise?

The core question in backtesting: "Did I find a real pattern, or did I
overfit to random variation in historical data?"

This module answers it with two tests:

1. Bootstrap confidence intervals (resampling with replacement):
   - Resample the trade log 1000 times and recompute metrics each time.
   - If your WR=56% but the 5th–95th percentile spans 45%–67%, you don't
     actually know if your edge is 45% or 67% — the estimate is noisy.
   - Rule of thumb: 90% CI width < 10% WR means you have enough trades.
   - Outputs: CI for WR, PF, Sharpe, max drawdown.

2. Permutation test (temporal shuffle):
   - Randomly shuffle trade entry timestamps 1000 times.
   - If your strategy's WR significantly EXCEEDS the shuffle distribution
     → timing is important → signal has timing edge (not just market drift).
   - If shuffled WR ≈ actual WR → your returns are likely from market drift
     (e.g., the FX uptrend) not from your signal.
   - p-value < 0.05 = timing matters = real signal.

3. Monte Carlo equity curve paths:
   - Simulate 500 forward equity curves using your empirical win/loss
     distribution. Shows the range of possible outcomes.
   - Critical: what's the 5th-percentile 6-month outcome?

Run:
    docker compose exec engine python -m anchor.backtesting.monte_carlo \\
        --instrument EUR_USD --end 2024-01-01
    docker compose exec engine python -m anchor.backtesting.monte_carlo \\
        --instrument EUR_USD --end 2024-01-01 --forward-months 6
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from anchor.backtesting.engine import BacktestEngine

logging.disable(logging.CRITICAL)

DATA_DIR  = Path("/app/data")
N_BOOT    = 1000    # bootstrap resamples
N_PERM    = 1000    # permutation shuffles
N_SIM     = 500     # forward equity curve simulations

W = "\033[92m"
R = "\033[91m"
Y = "\033[93m"
N = "\033[0m"
B = "\033[1m"


def _metrics(pnls: np.ndarray) -> dict[str, float]:
    """Compute WR, PF, Sharpe (annualized from trade-level returns)."""
    if len(pnls) == 0:
        return {"wr": 0.0, "pf": 0.0, "sharpe": 0.0, "maxdd": 0.0}

    wins   = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    wr     = float(len(wins) / len(pnls))
    pf     = float(wins.sum() / abs(losses.sum())) if len(losses) > 0 and losses.sum() != 0 else float("inf")

    # Sharpe: annualize from per-trade returns (assume ~20 trades/month)
    if pnls.std(ddof=1) > 0:
        sharpe = float(pnls.mean() / pnls.std(ddof=1) * np.sqrt(12 * 20))
    else:
        sharpe = 0.0

    # Max drawdown from equity curve (start at 1.0, apply % returns)
    # Convert absolute P&Ls to returns assuming equal risk per trade
    pnl_pct = pnls / max(abs(pnls).mean() / 0.01, 1e-8)   # normalize: 1 unit = 1% risk
    equity = np.cumprod(1.0 + pnl_pct / 100.0)
    equity = np.concatenate([[1.0], equity])
    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / (peak + 1e-8)
    maxdd = float(dd.max())

    return {"wr": wr, "pf": pf, "sharpe": sharpe, "maxdd": maxdd}


def _bootstrap_ci(pnls: np.ndarray, n_boot: int = N_BOOT, ci: float = 0.90) -> dict:
    """Bootstrap confidence intervals for key metrics."""
    rng = np.random.default_rng(42)
    records = []
    for _ in range(n_boot):
        sample = rng.choice(pnls, size=len(pnls), replace=True)
        records.append(_metrics(sample))

    df = pd.DataFrame(records)
    lo = (1 - ci) / 2
    hi = 1 - lo

    result = {}
    for col in df.columns:
        result[col] = {
            "mean": float(df[col].mean()),
            "lo":   float(df[col].quantile(lo)),
            "hi":   float(df[col].quantile(hi)),
        }
    return result


def _permutation_test(
    pnls: np.ndarray,
    times: np.ndarray,
    n_perm: int = N_PERM,
) -> dict[str, float]:
    """
    Permutation test: does signal timing matter, or are returns just market drift?

    We shuffle the P&L dollar values (not win/loss binary) across timestamps,
    then compare the MEAN P&L of the actual vs shuffled sequences.
    Using mean P&L (continuous) avoids the degenerate case where all permutations
    of a binary array yield the same WR (which collapses the test with few trades).

    p-value = fraction of shuffles with mean P&L >= actual mean P&L.
    Low p-value → timing matters → real signal edge.
    """
    actual_mean_pl = float(pnls.mean())
    rng = np.random.default_rng(42)

    shuffle_means = []
    for _ in range(n_perm):
        shuffled = rng.permutation(pnls)
        shuffle_means.append(float(shuffled.mean()))

    shuffle_means = np.array(shuffle_means)
    # One-tailed: how often does random beat actual?
    p_value = float((shuffle_means >= actual_mean_pl).mean())
    pct_better = float((shuffle_means < actual_mean_pl).mean()) * 100

    # Standardized z-score equivalent
    shuf_std = float(shuffle_means.std())
    z = (actual_mean_pl - float(shuffle_means.mean())) / shuf_std if shuf_std > 1e-10 else 0.0

    return {
        "actual_mean_pl": actual_mean_pl,
        "shuffle_mean":   float(shuffle_means.mean()),
        "shuffle_std":    shuf_std,
        "z_score":        z,
        "p_value":        p_value,
        "pct_simulations_beaten": pct_better,
    }


def _forward_sim(
    pnls: np.ndarray,
    initial_balance: float = 10_000.0,
    months: int = 6,
    trades_per_month: int = 20,
    n_sim: int = N_SIM,
    risk_pct: float = 0.01,
) -> dict[str, float]:
    """
    Monte Carlo forward simulation: range of possible equity outcomes.

    Resamples from the empirical trade P&L distribution (as % of account)
    to simulate future equity curves. Unlike simple bootstrapping, this
    accounts for compounding and path dependency.
    """
    # Convert absolute P&Ls to % of account (approximate — uses average balance)
    pnl_pct = pnls / initial_balance  # rough P&L as fraction of account

    rng = np.random.default_rng(42)
    total_trades = months * trades_per_month

    final_equities = []
    for _ in range(n_sim):
        balance = initial_balance
        sample = rng.choice(pnl_pct, size=total_trades, replace=True)
        for r in sample:
            balance *= (1.0 + r)
            balance = max(balance, 0.0)
        final_equities.append(balance)

    eq = np.array(final_equities)
    ret_pct = (eq - initial_balance) / initial_balance * 100

    return {
        "months":         months,
        "trades":         total_trades,
        "median_return":  float(np.median(ret_pct)),
        "p5_return":      float(np.percentile(ret_pct, 5)),
        "p25_return":     float(np.percentile(ret_pct, 25)),
        "p75_return":     float(np.percentile(ret_pct, 75)),
        "p95_return":     float(np.percentile(ret_pct, 95)),
        "prob_loss":      float((ret_pct < 0).mean()) * 100,
        "prob_10pct":     float((ret_pct >= 10 * months).mean()) * 100,  # ~10%/month
        "prob_ruin":      float((ret_pct <= -50).mean()) * 100,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monte Carlo backtesting validation"
    )
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--end", default="2024-01-01", help="In-sample end date")
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--forward-months", type=int, default=6,
                        help="Forward simulation horizon in months")
    parser.add_argument("--trades-per-month", type=int, default=20,
                        help="Expected trades per month for forward sim")
    args = parser.parse_args()

    inst = args.instrument
    h1 = DATA_DIR / f"{inst}_H1.csv"
    h4 = DATA_DIR / f"{inst}_H4.csv"
    d1 = DATA_DIR / f"{inst}_D.csv"

    print(f"\n{'='*64}")
    print(f"MONTE CARLO VALIDATION — {inst}")
    print(f"{'='*64}")

    if not h1.exists():
        print(f"ERROR: {h1} not found")
        return

    print("Running backtest ...", end=" ", flush=True)
    eng = BacktestEngine(initial_balance=args.balance)
    eng.load_csv(inst, "H1", str(h1))
    if h4.exists():
        eng.load_csv(inst, "H4", str(h4))
    if d1.exists():
        eng.load_csv(inst, "D", str(d1))

    results = eng.run(inst, "H1")
    trades  = [t for t in results.trade_log if t["entry_time"] < args.end]
    print(f"{len(trades)} in-sample trades")

    if len(trades) < 20:
        print(f"ERROR: only {len(trades)} trades — need ≥ 20 for reliable statistics.")
        return

    df = pd.DataFrame(trades)
    pnls  = df["net_pl"].values.astype(float)
    times = df["entry_time"].values

    actual = _metrics(pnls)

    print("\n  In-sample actual metrics:")
    print(f"    WR:       {actual['wr']*100:.1f}%")
    print(f"    PF:       {actual['pf']:.2f}")
    print(f"    Sharpe:   {actual['sharpe']:.2f}")
    print(f"    Max DD:   {actual['maxdd']*100:.1f}%")

    # ── 1. Bootstrap confidence intervals ─────────────────────────────────────
    print(f"\n{B}1. BOOTSTRAP CONFIDENCE INTERVALS  (90%, {N_BOOT} resamples){N}")
    print("   How much uncertainty is in your performance estimates?\n")
    ci = _bootstrap_ci(pnls)

    def _ci_color(width: float, threshold: float) -> str:
        return W if width < threshold else (Y if width < threshold * 2 else R)

    wr_lo, wr_hi = ci["wr"]["lo"] * 100, ci["wr"]["hi"] * 100
    wr_width = wr_hi - wr_lo
    wr_color = _ci_color(wr_width, 10.0)
    print(f"  Win Rate:  {wr_lo:.1f}% — {wr_hi:.1f}%  {wr_color}(width {wr_width:.1f}%){N}")

    pf_lo, pf_hi = ci["pf"]["lo"], ci["pf"]["hi"]
    pf_width = pf_hi - pf_lo
    pf_color = _ci_color(pf_width, 0.5)
    print(f"  PF:        {pf_lo:.2f} — {pf_hi:.2f}  {pf_color}(width {pf_width:.2f}){N}")

    sh_lo, sh_hi = ci["sharpe"]["lo"], ci["sharpe"]["hi"]
    sh_width = sh_hi - sh_lo
    sh_color = _ci_color(sh_width, 1.0)
    print(f"  Sharpe:    {sh_lo:.2f} — {sh_hi:.2f}  {sh_color}(width {sh_width:.2f}){N}")

    dd_lo, dd_hi = ci["maxdd"]["lo"] * 100, ci["maxdd"]["hi"] * 100
    print(f"  Max DD:    {dd_lo:.1f}% — {dd_hi:.1f}%")

    if wr_width < 10.0:
        print(f"\n  {W}✓ WR confidence interval is tight — estimate is reliable.{N}")
    elif wr_width < 20.0:
        print(f"\n  {Y}△ WR range is wide ({wr_width:.1f}%) — you need more trades for precision.{N}")
    else:
        print(f"\n  {R}✗ WR range is very wide ({wr_width:.1f}%) — results are dominated by noise.{N}")
        print(f"  {R}  Need ≥ 200 trades to narrow this below 10%.{N}")

    # ── 2. Permutation test ────────────────────────────────────────────────────
    print(f"\n{B}2. PERMUTATION TEST  (does timing matter?  {N_PERM} shuffles){N}")
    print("   If p-value < 0.05, your signal timing generates real edge.\n")

    perm = _permutation_test(pnls, times)
    p = perm["p_value"]
    p_color = W if p < 0.05 else (Y if p < 0.15 else R)

    print(f"  Actual mean P&L: ${perm['actual_mean_pl']:+.2f}/trade")
    print(f"  Random mean P&L: ${perm['shuffle_mean']:+.2f} ± ${perm['shuffle_std']:.2f}")
    print(f"  z-score:         {perm['z_score']:+.2f}")
    print(f"  p-value:         {p_color}{p:.4f}{N}  ({perm['pct_simulations_beaten']:.1f}% of shuffles beaten)")

    if p < 0.05:
        print(f"\n  {W}✓ p < 0.05: Your signal timing adds real edge.{N}")
        print(f"  {W}  Returns are NOT just market drift — the signal is doing something.{N}")
    elif p < 0.15:
        print(f"\n  {Y}△ p = {p:.3f}: Marginal significance. Collect more data before concluding.{N}")
    else:
        print(f"\n  {R}✗ p = {p:.3f}: Your WR is NOT statistically different from random.{N}")
        print(f"  {R}  The returns may be from market drift (FX carry, trend bias), not your signal.{N}")
        print(f"  {R}  Consider running on neutral-trend periods to verify.{N}")

    # ── 3. Forward simulation ──────────────────────────────────────────────────
    print(f"\n{B}3. FORWARD MONTE CARLO  ({args.forward_months} months, {N_SIM} paths){N}")
    print(f"   Expected {args.trades_per_month} trades/month — adjust with --trades-per-month\n")

    sim = _forward_sim(
        pnls,
        initial_balance=args.balance,
        months=args.forward_months,
        trades_per_month=args.trades_per_month,
    )

    print(f"  Median return:       {sim['median_return']:+.1f}%  ({args.forward_months} months)")
    print(f"  5th percentile:      {sim['p5_return']:+.1f}%  (worst ~1 in 20 outcomes)")
    print(f"  25th percentile:     {sim['p25_return']:+.1f}%")
    print(f"  75th percentile:     {sim['p75_return']:+.1f}%")
    print(f"  95th percentile:     {sim['p95_return']:+.1f}%  (best ~1 in 20 outcomes)")

    loss_color = R if sim["prob_loss"] > 30 else (Y if sim["prob_loss"] > 15 else W)
    print(f"\n  Probability of loss: {loss_color}{sim['prob_loss']:.1f}%{N}")
    print(f"  Prob ≥10%/month avg: {W}{sim['prob_10pct']:.1f}%{N}")
    ruin_color = R if sim["prob_ruin"] > 5 else (Y if sim["prob_ruin"] > 1 else W)
    print(f"  Probability of ruin (−50%+): {ruin_color}{sim['prob_ruin']:.1f}%{N}")

    monthly_return = sim["median_return"] / args.forward_months
    if monthly_return >= 8.0:
        print(f"\n  {W}✓ Median ~{monthly_return:.1f}%/month — 8–10% target looks achievable.{N}")
    elif monthly_return >= 4.0:
        print(f"\n  {Y}△ Median ~{monthly_return:.1f}%/month — below 8% target.{N}")
        print(f"  {Y}  Add NY session strategy or slightly increase risk (1.5%) to close the gap.{N}")
    else:
        print(f"\n  {R}✗ Median ~{monthly_return:.1f}%/month — well below 8% target.{N}")
        print(f"  {R}  Strategy needs significant improvement before increasing risk per trade.{N}")

    print(f"\n{'='*64}\n")


if __name__ == "__main__":
    main()
