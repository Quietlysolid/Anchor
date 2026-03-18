"""
Walk-Forward Validation for LCR Strategy.

Splits 8 years of H1 data into 4 sequential folds and runs the LCR backtest
on the OUT-OF-SAMPLE portion of each fold. If edge persists across all folds,
it's real. If it concentrates in 1-2 folds, it's regime-specific.

Fold structure (2-year train, 2-year test, rolling forward):
  Fold 1: train 2018–2019, test 2020–2021
  Fold 2: train 2019–2020, test 2021–2022
  Fold 3: train 2020–2021, test 2022–2023
  Fold 4: train 2021–2022, test 2023–2024

The LCR engine has no fitted parameters that use the training window — it uses
fixed thresholds (confluence ≥ 0.55, ATR multipliers, etc). So "train" here
means the warmup/lookback period used for indicator calculation only. The
walk-forward primarily answers: does the edge hold in each unseen 2-year OOS block?

Usage:
    python -m anchor.backtesting.lcr_walkforward \\
        --instrument EUR_USD \\
        --h1-csv data/EUR_USD_H1.csv

    # All pairs
    for pair in EUR_USD GBP_USD USD_JPY; do
        python -m anchor.backtesting.lcr_walkforward \\
            --instrument $pair --h1-csv data/${pair}_H1.csv
    done
"""
from __future__ import annotations

import argparse

import pandas as pd

from anchor.backtesting.lcr_backtest import LCRBacktestEngine, _load_csv


# Each fold: (train_start, test_start, test_end)  — all UTC strings
FOLDS = [
    ("2018-01-01", "2020-01-01", "2022-01-01"),
    ("2019-01-01", "2021-01-01", "2023-01-01"),
    ("2020-01-01", "2022-01-01", "2024-01-01"),
    ("2021-01-01", "2023-01-01", "2025-01-01"),
]


def run_walkforward(instrument: str, df_h1: pd.DataFrame) -> None:
    print(f"\n{'='*65}")
    print(f"LCR Walk-Forward Validation — {instrument}")
    print(f"{'='*65}")
    print(f"  {'Fold':<8} {'OOS Period':<24} {'Trades':>7} {'WR':>7} {'PF':>7} {'Net%':>8} {'MaxDD':>8}")
    print(f"  {'-'*8} {'-'*24} {'-'*7} {'-'*7} {'-'*7} {'-'*8} {'-'*8}")

    fold_pfs = []
    fold_wrs = []

    for i, (train_start, test_start, test_end) in enumerate(FOLDS, 1):
        # Include the train window so lookback indicators are warm at test_start
        engine = LCRBacktestEngine(initial_balance=10_000.0)
        results = engine.run(
            instrument=instrument,
            df_h1=df_h1,
            start=train_start,
            end=test_end,
        )

        if results.get("error"):
            print(f"  Fold {i}   ERROR: {results['error']}")
            continue

        # Filter trades to the OOS window only
        oos_trades = [
            t for t in results["trades"]
            if test_start <= t["entry_time"][:10] < test_end
        ]

        if not oos_trades:
            print(f"  Fold {i}   {test_start[:7]}–{test_end[:7]}   NO TRADES IN OOS WINDOW")
            continue

        wins        = [t for t in oos_trades if t["pl_pips"] > 0]
        losses      = [t for t in oos_trades if t["pl_pips"] <= 0]
        gross_p     = sum(t["pl_pips"] for t in wins)
        gross_l     = abs(sum(t["pl_pips"] for t in losses))
        pf          = gross_p / gross_l if gross_l > 0 else float("inf")
        wr          = len(wins) / len(oos_trades) * 100

        # Net % from OOS trades only (compound)
        bal = 10_000.0
        for t in oos_trades:
            bal *= (1 + t["pl_pct"] / 100)
        net_pct = (bal - 10_000.0) / 10_000.0 * 100

        # Max drawdown in OOS window
        import numpy as np
        equity = [10_000.0]
        b = 10_000.0
        for t in oos_trades:
            b *= (1 + t["pl_pct"] / 100)
            equity.append(b)
        eq = np.array(equity)
        peak = np.maximum.accumulate(eq)
        max_dd = float(np.min((eq - peak) / peak)) * 100

        fold_pfs.append(pf)
        fold_wrs.append(wr)

        oos_label = f"{test_start[:7]} – {test_end[:7]}"
        pf_flag   = "" if pf >= 1.0 else " ⚠"
        print(f"  Fold {i}   {oos_label:<24} {len(oos_trades):>7} {wr:>6.1f}% {pf:>7.3f}{pf_flag} {net_pct:>7.1f}% {max_dd:>7.1f}%")

    print(f"  {'-'*65}")
    if fold_pfs:
        avg_pf = sum(fold_pfs) / len(fold_pfs)
        avg_wr = sum(fold_wrs) / len(fold_wrs)
        passing = sum(1 for pf in fold_pfs if pf >= 1.0)
        print(f"  {'Average':<8} {'':24} {'':>7} {avg_wr:>6.1f}% {avg_pf:>7.3f}  ({passing}/{len(fold_pfs)} folds profitable)")

        print("\n  Verdict:")
        if avg_pf >= 1.3 and passing == len(fold_pfs):
            print(f"  ✓ STRONG EDGE — PF ≥ 1.3 across all {len(fold_pfs)} folds. Edge is consistent.")
        elif avg_pf >= 1.0 and passing >= 3:
            print(f"  ~ MODERATE EDGE — Positive in {passing}/{len(fold_pfs)} folds. Monitor PF monthly.")
        elif passing >= 2:
            print(f"  ! WEAK / REGIME-SPECIFIC — Only profitable in {passing}/{len(fold_pfs)} folds.")
            print("    Consider restricting to RANGING regime only (HMM gate).")
        else:
            print("  ✗ NO CONSISTENT EDGE — Profitable in <50% of OOS folds. Do not trade live.")

    print(f"{'='*65}\n")


def main():
    parser = argparse.ArgumentParser(description="LCR walk-forward validation")
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--h1-csv", required=True)
    args = parser.parse_args()

    df_h1 = _load_csv(args.h1_csv)
    run_walkforward(args.instrument, df_h1)


if __name__ == "__main__":
    main()
