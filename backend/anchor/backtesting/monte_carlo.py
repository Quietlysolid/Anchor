"""Monte Carlo validation — is your edge real or just noise?

This module now uses shared validation helpers with:
- moving-block bootstrap CIs
- a one-sample sign-flip randomization test for mean trade edge
- block-resampled forward equity simulation
- yearly and optional regime-conditioned summaries
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from anchor.backtesting.engine import BacktestEngine
from anchor.backtesting.validation_utils import (
    bootstrap_confidence_intervals,
    compute_trade_metrics,
    forward_equity_simulation,
    regime_trade_summary,
    sign_flip_test,
    yearly_trade_summary,
)

logging.disable(logging.CRITICAL)

DATA_DIR = Path("/app/data")
N_BOOT = 1000
N_PERM = 5000
N_SIM = 500

W = "\033[92m"
R = "\033[91m"
Y = "\033[93m"
N = "\033[0m"
B = "\033[1m"


def _ci_color(width: float, threshold: float) -> str:
    return W if width < threshold else (Y if width < threshold * 2 else R)


def _regime_labels(trades: pd.DataFrame, event_mode: str = "off") -> list[str]:
    labels = []
    for _, row in trades.iterrows():
        entry = pd.Timestamp(row["entry_time"], tz="UTC")
        session = row.get("session") or "UNKNOWN"
        atr = float(row.get("atr") or 0.0)

        vol_label = "high_vol" if atr > 0.01 else "normal_vol"
        if event_mode == "calendar":
            event_hour = entry.hour in {8, 9, 13, 14}
            event_label = "event_window" if event_hour else "non_event"
        else:
            event_label = "all_days"

        labels.append(f"{session.lower()}|{vol_label}|{event_label}")
    return labels


def main() -> None:
    parser = argparse.ArgumentParser(description="Monte Carlo backtesting validation")
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--end", default="2024-01-01", help="In-sample end date (exclusive)")
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--forward-months", type=int, default=6, help="Forward simulation horizon in months")
    parser.add_argument("--trades-per-month", type=int, default=20, help="Expected trades per month for forward sim")
    parser.add_argument("--bootstrap", choices=["block", "iid"], default="block", help="Bootstrap method")
    parser.add_argument("--block-size", type=int, default=5, help="Trade block size for block bootstrap / simulation")
    parser.add_argument(
        "--event-mode",
        choices=["off", "calendar"],
        default="off",
        help="Optional rough event-window tagging for regime summaries",
    )
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
    trades = [t for t in results.trade_log if t["entry_time"] < args.end]
    print(f"{len(trades)} in-sample trades")

    if len(trades) < 20:
        print(f"ERROR: only {len(trades)} trades — need >= 20 for reliable statistics.")
        return

    df = pd.DataFrame(trades)
    pnls = df["net_pl"].values.astype(float)

    actual = compute_trade_metrics(pnls)
    print("\n  In-sample actual metrics:")
    print(f"    WR:       {actual['wr']*100:.1f}%")
    print(f"    PF:       {actual['pf']:.2f}")
    print(f"    Mean P&L: ${actual['mean_pl']:+.2f}/trade")
    print(f"    Sharpe:   {actual['sharpe']:.2f}")
    print(f"    Sortino:  {actual['sortino']:.2f}")
    print(f"    Max DD:   {actual['maxdd']*100:.1f}%")

    print(f"\n{B}1. BOOTSTRAP CONFIDENCE INTERVALS  (90%, {N_BOOT} resamples, {args.bootstrap}){N}")
    print("   Block bootstrap is the default because adjacent trades are not independent.\n")
    ci = bootstrap_confidence_intervals(
        pnls,
        n_boot=N_BOOT,
        method=args.bootstrap,
        block_size=args.block_size,
    )

    wr_lo, wr_hi = ci["wr"]["lo"] * 100, ci["wr"]["hi"] * 100
    wr_width = wr_hi - wr_lo
    print(f"  Win Rate:  {wr_lo:.1f}% — {wr_hi:.1f}%  {_ci_color(wr_width, 10.0)}(width {wr_width:.1f}%){N}")

    pf_lo, pf_hi = ci["pf"]["lo"], ci["pf"]["hi"]
    pf_width = pf_hi - pf_lo
    print(f"  PF:        {pf_lo:.2f} — {pf_hi:.2f}  {_ci_color(pf_width, 0.5)}(width {pf_width:.2f}){N}")

    sh_lo, sh_hi = ci["sharpe"]["lo"], ci["sharpe"]["hi"]
    sh_width = sh_hi - sh_lo
    print(f"  Sharpe:    {sh_lo:.2f} — {sh_hi:.2f}  {_ci_color(sh_width, 1.0)}(width {sh_width:.2f}){N}")

    dd_lo, dd_hi = ci["maxdd"]["lo"] * 100, ci["maxdd"]["hi"] * 100
    print(f"  Max DD:    {dd_lo:.1f}% — {dd_hi:.1f}%")

    if wr_width < 10.0:
        print(f"\n  {W}WR estimate is reasonably tight.{N}")
    elif wr_width < 20.0:
        print(f"\n  {Y}WR estimate is still wide; more trades would materially improve precision.{N}")
    else:
        print(f"\n  {R}WR estimate is dominated by uncertainty; do not lean heavily on point estimates yet.{N}")

    print(f"\n{B}2. RANDOMIZATION TEST  (mean trade edge > 0?  {N_PERM} sign flips){N}")
    print("   This replaces the old broken permutation test. It tests whether the observed")
    print("   mean trade P&L is stronger than random sign assignment under the null.\n")

    perm = sign_flip_test(pnls, n_perm=N_PERM)
    p = perm["p_value"]
    p_color = W if p < 0.05 else (Y if p < 0.15 else R)

    print(f"  Actual mean P&L: ${perm['actual_mean_pl']:+.2f}/trade")
    print(f"  Null mean P&L:   ${perm['null_mean']:+.2f} ± ${perm['null_std']:.2f}")
    print(f"  z-score:         {perm['z_score']:+.2f}")
    print(f"  p-value:         {p_color}{p:.4f}{N}  ({perm['pct_simulations_beaten']:.1f}% of null draws beaten)")

    if p < 0.05:
        print(f"\n  {W}Observed trade edge is statistically distinguishable from zero at the 5% level.{N}")
    elif p < 0.15:
        print(f"\n  {Y}Evidence is marginal. Treat the strategy as provisional rather than validated.{N}")
    else:
        print(f"\n  {R}No strong evidence that mean trade edge exceeds noise.{N}")

    print(f"\n{B}3. FORWARD MONTE CARLO  ({args.forward_months} months, {N_SIM} paths){N}")
    print(f"   Uses block-resampled trade returns with block size {args.block_size}.\n")

    sim = forward_equity_simulation(
        pnls,
        initial_balance=args.balance,
        months=args.forward_months,
        trades_per_month=args.trades_per_month,
        n_sim=N_SIM,
        block_size=args.block_size,
    )

    print(f"  Median return:       {sim['median_return']:+.1f}%")
    print(f"  5th percentile:      {sim['p5_return']:+.1f}%")
    print(f"  25th percentile:     {sim['p25_return']:+.1f}%")
    print(f"  75th percentile:     {sim['p75_return']:+.1f}%")
    print(f"  95th percentile:     {sim['p95_return']:+.1f}%")
    print(f"  Probability of loss: {sim['prob_loss']:.1f}%")
    print(f"  Prob >=10%/month avg: {sim['prob_10pct']:.1f}%")
    print(f"  Probability of ruin (-50%+): {sim['prob_ruin']:.1f}%")

    print(f"\n{B}4. YEARLY STABILITY{N}\n")
    yearly = yearly_trade_summary(df)
    print(f"  {'Year':<6} {'Trades':>7} {'WR%':>7} {'PF':>7} {'Mean$':>9} {'MaxDD%':>8}")
    for row in yearly:
        pf = row["pf"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(
            f"  {row['year']:<6} {row['n_trades']:>7} {row['wr']*100:>6.1f}% "
            f"{pf_str:>7} {row['mean_pl']:>+8.2f} {row['maxdd']*100:>7.1f}%"
        )

    print(f"\n{B}5. REGIME SNAPSHOT{N}\n")
    regime_rows = regime_trade_summary(df, _regime_labels(df, event_mode=args.event_mode))
    print(f"  {'Bucket':<30} {'Trades':>7} {'WR%':>7} {'PF':>7} {'Mean$':>9}")
    for label, metrics in regime_rows.items():
        if metrics["n_trades"] < 10:
            continue
        pf = metrics["pf"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(
            f"  {label:<30} {int(metrics['n_trades']):>7} {metrics['wr']*100:>6.1f}% "
            f"{pf_str:>7} {metrics['mean_pl']:>+8.2f}"
        )

    print(f"\n{'='*64}\n")


if __name__ == "__main__":
    main()
