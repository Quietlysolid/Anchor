"""Common validation pass for focused research sleeves exported as CSV observations."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from anchor.backtesting.validation_utils import (
    bootstrap_confidence_intervals,
    compute_trade_metrics,
    forward_equity_simulation,
    sign_flip_test,
)

N_BOOT = 1000
N_PERM = 5000
N_SIM = 500


def _print_section(title: str) -> None:
    print(f"\n{'=' * 88}")
    print(title)
    print(f"{'=' * 88}")


def _validate_bucket(
    label: str,
    group: pd.DataFrame,
    pnl_col: str,
    block_size: int,
    forward_months: int,
    trades_per_month: int,
) -> None:
    pnls = group[pnl_col].astype(float).values
    actual = compute_trade_metrics(pnls)
    ci = bootstrap_confidence_intervals(pnls, n_boot=N_BOOT, method="block", block_size=block_size)
    sign = sign_flip_test(pnls, n_perm=N_PERM)
    sim = forward_equity_simulation(
        pnls,
        months=forward_months,
        trades_per_month=trades_per_month,
        n_sim=N_SIM,
        block_size=block_size,
    )

    print(f"{label}")
    print(
        f"  Trades={int(actual['n_trades'])} WR={actual['wr']*100:.1f}% PF={actual['pf']:.3f} "
        f"Mean={actual['mean_pl']:+.2f} MaxDD={actual['maxdd']*100:.2f}%"
    )
    print(
        f"  90% CI: WR {ci['wr']['lo']*100:.1f}-{ci['wr']['hi']*100:.1f}% | "
        f"PF {ci['pf']['lo']:.2f}-{ci['pf']['hi']:.2f} | "
        f"Mean {ci['mean_pl']['lo']:+.2f}..{ci['mean_pl']['hi']:+.2f}"
    )
    print(
        f"  Sign-flip: p={sign['p_value']:.4f} z={sign['z_score']:+.2f} "
        f"beaten={sign['pct_simulations_beaten']:.1f}%"
    )
    print(
        f"  Forward {forward_months}m: median={sim['median_return']:+.1f}% "
        f"p5={sim['p5_return']:+.1f}% p95={sim['p95_return']:+.1f}% "
        f"loss_prob={sim['prob_loss']:.1f}%"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a focused research sleeve from exported observations")
    parser.add_argument("--csv", required=True, help="Path to observations CSV")
    parser.add_argument("--pnl-col", default="ret_pips_net")
    parser.add_argument("--group-col", default="pair")
    parser.add_argument("--filter-col", default=None)
    parser.add_argument("--filter-values", default=None)
    parser.add_argument("--min-trades", type=int, default=5)
    parser.add_argument("--block-size", type=int, default=3)
    parser.add_argument("--forward-months", type=int, default=6)
    parser.add_argument("--trades-per-month", type=int, default=2)
    args = parser.parse_args()

    path = Path(args.csv)
    if not path.exists():
        raise SystemExit(f"Missing CSV: {path}")

    df = pd.read_csv(path)
    if args.pnl_col not in df.columns:
        raise SystemExit(f"Missing PnL column: {args.pnl_col}")

    if args.filter_col and args.filter_values:
        if args.filter_col not in df.columns:
            raise SystemExit(f"Missing filter column: {args.filter_col}")
        allowed = {x.strip() for x in args.filter_values.split(",") if x.strip()}
        df = df[df[args.filter_col].astype(str).isin(allowed)].copy()

    _print_section("SLEEVE VALIDATION")
    print(f"CSV          : {path}")
    print(f"PnL column   : {args.pnl_col}")
    print(f"Group column : {args.group_col}")
    if args.filter_col and args.filter_values:
        print(f"Filter       : {args.filter_col} in {args.filter_values}")
    print(f"Rows         : {len(df)}")

    if len(df) >= args.min_trades:
        _print_section("COMBINED")
        _validate_bucket(
            "ALL",
            df,
            pnl_col=args.pnl_col,
            block_size=args.block_size,
            forward_months=args.forward_months,
            trades_per_month=args.trades_per_month,
        )

    if args.group_col in df.columns:
        _print_section("BY GROUP")
        counts = df.groupby(args.group_col).size().sort_values(ascending=False)
        for key, n in counts.items():
            if int(n) < args.min_trades:
                continue
            group = df[df[args.group_col] == key]
            _validate_bucket(
                f"{args.group_col}={key}",
                group,
                pnl_col=args.pnl_col,
                block_size=args.block_size,
                forward_months=args.forward_months,
                trades_per_month=max(1, int(round(len(group) / max(df['event_time'].nunique() if 'event_time' in df.columns else 6, 1)))),
            )


if __name__ == "__main__":
    main()
