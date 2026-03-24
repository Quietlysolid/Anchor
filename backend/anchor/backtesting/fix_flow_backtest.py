"""Research backtest for London benchmark fix flow."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from anchor.backtesting.event_study import _load_h1_csv
from anchor.backtesting.fix_calendar import build_fix_windows
from anchor.backtesting.validation_utils import compute_trade_metrics
from anchor.utils.math_utils import get_pip_size

DATA_DIR = Path("/app/data")
_ROUND_TRIP_SPREAD_PIPS = {
    "EUR_USD": 0.6,
    "GBP_USD": 0.8,
    "USD_JPY": 0.6,
}


def _window_move(df_h1: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.Timestamp, float, float] | None:
    entry_time = pd.Timestamp(start)
    if entry_time not in df_h1.index:
        idx = df_h1.index.searchsorted(start, side="left")
        if idx >= len(df_h1.index):
            return None
        entry_time = pd.Timestamp(df_h1.index[idx])

    exit_idx = df_h1.index.searchsorted(end, side="left")
    if exit_idx >= len(df_h1.index):
        return None
    exit_time = pd.Timestamp(df_h1.index[exit_idx])

    entry_open = float(df_h1.loc[entry_time, "open"])
    exit_close = float(df_h1.loc[exit_time, "close"])
    return exit_time, entry_open, exit_close


def _summarize(group: pd.DataFrame) -> dict[str, float | int]:
    pnl = group["ret_pips_net"].astype(float).values
    metrics = compute_trade_metrics(pnl)
    return {
        "n_trades": int(metrics["n_trades"]),
        "mean_ret_pips": round(float(np.mean(pnl)), 2),
        "median_ret_pips": round(float(np.median(pnl)), 2),
        "wr": round(metrics["wr"] * 100.0, 1),
        "pf": round(metrics["pf"], 3),
        "maxdd": round(metrics["maxdd"] * 100.0, 2),
    }


def run(args) -> int:
    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    h1_data: dict[str, pd.DataFrame] = {}
    for pair in pairs:
        try:
            h1_data[pair] = _load_h1_csv(pair, Path(args.data_dir))
        except FileNotFoundError:
            print(f"SKIP: missing H1 CSV for {pair}")

    if not h1_data:
        print("ERROR: no H1 data loaded")
        return 1

    windows = build_fix_windows(args.start, args.end, pre_hours=args.pre_hours, post_hours=args.post_hours)
    rows: list[dict[str, object]] = []

    for pair, df_h1 in h1_data.items():
        pip = get_pip_size(pair)
        cost_pips = _ROUND_TRIP_SPREAD_PIPS.get(pair, 0.0) * args.spread_mult + 2.0 * args.slippage_pips

        for fix in windows:
            pre = _window_move(df_h1, fix.pre_start_utc, fix.pre_end_utc)
            post = _window_move(df_h1, fix.fix_time_utc, fix.post_end_utc)
            if pre is None or post is None:
                continue

            _, pre_entry, pre_exit = pre
            _, post_entry, post_exit = post
            pre_move = pre_exit - pre_entry
            if abs(pre_move) < pip * args.min_pre_move_pips:
                continue

            direction = 1.0 if pre_move > 0 else -1.0
            continuation_pips = (direction * (post_exit - post_entry) / pip) - cost_pips
            reversal_pips = (-direction * (post_exit - post_entry) / pip) - cost_pips

            for setup, ret_pips_net in [
                ("post_fix_continuation", continuation_pips),
                ("post_fix_reversal", reversal_pips),
            ]:
                rows.append(
                    {
                        "pair": pair,
                        "trade_date": fix.trade_date.date().isoformat(),
                        "year": fix.trade_date.year,
                        "setup": setup,
                        "month_end": "ME" if fix.is_month_end else "REG",
                        "quarter_end": fix.is_quarter_end,
                        "pre_move_pips": abs(pre_move / pip),
                        "ret_pips_net": ret_pips_net,
                    }
                )

    if not rows:
        print("No fix-flow observations matched the current filters.")
        return 0

    obs = pd.DataFrame(rows)
    summary_rows = []
    for (pair, setup, month_end), group in obs.groupby(["pair", "setup", "month_end"]):
        summary_rows.append({"pair": pair, "setup": setup, "month_end": month_end, **_summarize(group)})
    summary = pd.DataFrame(summary_rows).sort_values(["setup", "month_end", "pf", "mean_ret_pips"], ascending=[True, True, False, False])

    delta_rows = []
    for (pair, setup), group in summary.groupby(["pair", "setup"]):
        if set(group["month_end"]) >= {"ME", "REG"}:
            me = group[group["month_end"] == "ME"].iloc[0]
            reg = group[group["month_end"] == "REG"].iloc[0]
            delta_rows.append(
                {
                    "pair": pair,
                    "setup": setup,
                    "me_minus_reg_mean_pips": round(float(me["mean_ret_pips"] - reg["mean_ret_pips"]), 2),
                    "me_minus_reg_wr": round(float(me["wr"] - reg["wr"]), 1),
                    "me_minus_reg_pf": round(float(me["pf"] - reg["pf"]), 3),
                }
            )
    delta = pd.DataFrame(delta_rows).sort_values(["setup", "me_minus_reg_mean_pips"], ascending=[True, False]) if delta_rows else pd.DataFrame()

    focused = summary[
        (summary["setup"] == args.focus_setup) &
        (summary["month_end"].isin([x.strip().upper() for x in args.focus_month_end.split(",") if x.strip()]))
    ].sort_values(["pf", "mean_ret_pips", "wr"], ascending=[False, False, False])

    yearly_rows = []
    for (pair, setup, year), group in obs.groupby(["pair", "setup", "year"]):
        yearly_rows.append({"pair": pair, "setup": setup, "year": int(year), **_summarize(group)})
    yearly = pd.DataFrame(yearly_rows).sort_values(["setup", "pair", "year"])

    print(f"\n{'=' * 92}")
    print("FIX FLOW BACKTEST")
    print(f"{'=' * 92}")
    print(f"Pairs        : {', '.join(sorted(h1_data.keys()))}")
    print(f"Pre window   : {args.pre_hours}h into London fix")
    print(f"Post window  : {args.post_hours}h after London fix")
    print(f"Min pre move : {args.min_pre_move_pips:.1f} pips")
    print(f"Spread mult  : {args.spread_mult}")
    print(f"Slippage     : {args.slippage_pips:.2f} pips/side")
    print(f"Observations : {len(obs)}")
    print(f"{'=' * 92}")
    print(f"{'Pair':<10} {'Setup':<22} {'Tag':<4} {'N':>4} {'MeanPips':>10} {'MedPips':>9} {'WR':>6} {'PF':>7} {'MaxDD':>7}")
    for _, row in summary.iterrows():
        print(
            f"{row['pair']:<10} {row['setup']:<22} {row['month_end']:<4} {int(row['n_trades']):>4} "
            f"{row['mean_ret_pips']:>10.2f} {row['median_ret_pips']:>9.2f} {row['wr']:>5.1f}% "
            f"{row['pf']:>7.3f} {row['maxdd']:>6.2f}%"
        )
    print(f"{'=' * 92}\n")

    if not delta.empty:
        print(f"{'=' * 92}")
        print("MONTH-END DELTA")
        print(f"{'=' * 92}")
        print(f"{'Pair':<10} {'Setup':<22} {'ME-REG Pips':>12} {'ME-REG WR':>10} {'ME-REG PF':>10}")
        for _, row in delta.iterrows():
            print(
                f"{row['pair']:<10} {row['setup']:<22} {row['me_minus_reg_mean_pips']:>12.2f} "
                f"{row['me_minus_reg_wr']:>9.1f} {row['me_minus_reg_pf']:>10.3f}"
            )
        print(f"{'=' * 92}\n")

    if not focused.empty:
        print(f"{'=' * 92}")
        print("QUANT CUT")
        print(f"{'=' * 92}")
        print(f"Focus setup  : {args.focus_setup} | tags: {args.focus_month_end}")
        print(f"{'Pair':<10} {'Tag':<4} {'N':>4} {'MeanPips':>10} {'WR':>6} {'PF':>7} {'MaxDD':>7}")
        for _, row in focused.iterrows():
            print(
                f"{row['pair']:<10} {row['month_end']:<4} {int(row['n_trades']):>4} "
                f"{row['mean_ret_pips']:>10.2f} {row['wr']:>5.1f}% "
                f"{row['pf']:>7.3f} {row['maxdd']:>6.2f}%"
            )
        print(f"{'=' * 92}\n")

    print(f"{'=' * 92}")
    print("YEARLY STABILITY")
    print(f"{'=' * 92}")
    print(f"{'Pair':<10} {'Setup':<22} {'Year':>4} {'N':>4} {'MeanPips':>10} {'WR':>6} {'PF':>7} {'MaxDD':>7}")
    for _, row in yearly.iterrows():
        print(
            f"{row['pair']:<10} {row['setup']:<22} {int(row['year']):>4} {int(row['n_trades']):>4} "
            f"{row['mean_ret_pips']:>10.2f} {row['wr']:>5.1f}% "
            f"{row['pf']:>7.3f} {row['maxdd']:>6.2f}%"
        )
    print(f"{'=' * 92}\n")

    if args.export_csv:
        export_path = Path(args.export_csv)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        obs.to_csv(export_path.with_suffix(".observations.csv"), index=False)
        summary.to_csv(export_path.with_suffix(".summary.csv"), index=False)
        yearly.to_csv(export_path.with_suffix(".yearly.csv"), index=False)
        if not focused.empty:
            focused.to_csv(export_path.with_suffix(".focus.csv"), index=False)
        print(f"Saved: {export_path.with_suffix('.observations.csv')}")
        print(f"Saved: {export_path.with_suffix('.summary.csv')}")
        print(f"Saved: {export_path.with_suffix('.yearly.csv')}")
        if not focused.empty:
            print(f"Saved: {export_path.with_suffix('.focus.csv')}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Research backtest for London benchmark-fix flow")
    parser.add_argument("--pairs", default="EUR_USD,GBP_USD,USD_JPY")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--pre-hours", type=int, default=1)
    parser.add_argument("--post-hours", type=int, default=1)
    parser.add_argument("--min-pre-move-pips", type=float, default=5.0)
    parser.add_argument("--spread-mult", type=float, default=1.0)
    parser.add_argument("--slippage-pips", type=float, default=0.0)
    parser.add_argument("--focus-setup", default="post_fix_continuation")
    parser.add_argument("--focus-month-end", default="REG,ME")
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--export-csv", default=None)
    raise SystemExit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
