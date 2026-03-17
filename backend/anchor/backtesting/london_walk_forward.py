"""
Walk-Forward Validation for London Trend Strategy.

Runs the primary trend-following signal engine (BacktestEngine) across
independent 1-year OOS windows from 2018 to 2024.

Usage:
    python -m anchor.backtesting.london_walk_forward \\
        --instrument EUR_USD \\
        --h1-csv  data/EUR_USD_H1.csv \\
        --h4-csv  data/EUR_USD_H4.csv \\
        --d-csv   data/EUR_USD_D.csv

    # All live pairs
    for pair in EUR_USD GBP_USD NZD_USD USD_CAD EUR_JPY AUD_USD; do
        python -m anchor.backtesting.london_walk_forward \\
            --instrument $pair \\
            --h1-csv data/${pair}_H1.csv \\
            --h4-csv data/${pair}_H4.csv \\
            --d-csv  data/${pair}_D.csv
    done
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

from anchor.backtesting.engine import BacktestEngine

_WINDOWS = [
    ("2018-01-01", "2018-12-31"),
    ("2019-01-01", "2019-12-31"),
    ("2020-01-01", "2020-12-31"),
    ("2021-01-01", "2021-12-31"),
    ("2022-01-01", "2022-12-31"),
    ("2023-01-01", "2023-12-31"),
    ("2024-01-01", "2024-12-31"),
]

_REGIME_NOTES = {
    "2018": "Fed hike cycle, USD strength",
    "2019": "Trade war, late-cycle ranging",
    "2020": "COVID — extreme vol / regime breaks",
    "2021": "Recovery, low vol, melt-up",
    "2022": "Inflation shock, massive trend year",
    "2023": "Banking stress, choppy recovery",
    "2024": "Rate-cut anticipation, mixed",
}


def _load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"])
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def run_walk_forward(
    instrument: str,
    df_h1: pd.DataFrame,
    df_h4: pd.DataFrame | None = None,
    df_d: pd.DataFrame | None = None,
    initial_balance: float = 10_000.0,
) -> list[dict[str, Any]]:
    results = []

    for start, end in _WINDOWS:
        year = start[:4]

        # Check data coverage
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts   = pd.Timestamp(end,   tz="UTC")
        window_h1 = df_h1[(df_h1["time"] >= start_ts) & (df_h1["time"] <= end_ts)]
        if len(window_h1) < 100:
            results.append({
                "year": year, "regime": _REGIME_NOTES.get(year, ""),
                "skipped": True, "reason": f"only {len(window_h1)} H1 bars",
            })
            continue

        # Load full history so indicators have warm-up data, but slice H1 to the window
        # so the engine only iterates bars within the OOS year.
        # H4/D: load full — engine reads them as lookback context only.
        engine = BacktestEngine(initial_balance=initial_balance)
        win_h1 = df_h1[df_h1["time"] <= end_ts].copy()  # full history up to end of window
        # Trim the front: keep 200 warm-up bars before window start, then window bars
        pre_start = df_h1[df_h1["time"] < start_ts].tail(200)
        in_window = df_h1[(df_h1["time"] >= start_ts) & (df_h1["time"] <= end_ts)]
        win_h1 = pd.concat([pre_start, in_window]).drop_duplicates()
        engine.load_df(instrument, "H1", win_h1)
        if df_h4 is not None:
            win_h4 = df_h4[df_h4["time"] <= end_ts].copy()
            engine.load_df(instrument, "H4", win_h4)
        if df_d is not None:
            win_d = df_d[df_d["time"] <= end_ts].copy()
            engine.load_df(instrument, "D", win_d)

        try:
            res = engine.run(instrument=instrument, timeframe="H1")
        except Exception as exc:
            results.append({
                "year": year, "regime": _REGIME_NOTES.get(year, ""),
                "skipped": True, "reason": str(exc)[:60],
            })
            continue

        # Filter trade_log to only trades that opened within the OOS window
        # (warm-up bars may have generated trades before window start)
        window_trades = [
            t for t in (res.trade_log or [])
            if pd.Timestamp(t["entry_time"]) >= start_ts
        ]

        if not window_trades:
            results.append({
                "year": year, "regime": _REGIME_NOTES.get(year, ""),
                "skipped": True, "reason": "no trades in window",
            })
            continue

        import numpy as np
        wins   = [t for t in window_trades if t.get("pnl_pct", t.get("pnl", 0)) > 0]
        losses = [t for t in window_trades if t.get("pnl_pct", t.get("pnl", 0)) <= 0]
        pips_key = "pnl_pips" if "pnl_pips" in window_trades[0] else "pnl"
        gross_p = sum(t[pips_key] for t in wins   if t[pips_key] > 0)
        gross_l = abs(sum(t[pips_key] for t in losses if t[pips_key] <= 0))
        pf = gross_p / gross_l if gross_l > 0 else float("inf")

        # Net % from trade log
        pnl_pcts = [t.get("pnl_pct", 0) for t in window_trades]
        net_pct  = sum(pnl_pcts) * 100 if max(abs(p) for p in pnl_pcts) < 1 else sum(pnl_pcts)

        # Max drawdown from equity curve
        equity = [initial_balance]
        bal = initial_balance
        for t in window_trades:
            bal *= (1 + t.get("pnl_pct", 0))
            equity.append(bal)
        equity_arr = np.array(equity)
        peak = np.maximum.accumulate(equity_arr)
        dd   = (equity_arr - peak) / peak
        max_dd = float(np.min(dd)) * 100

        results.append({
            "year":             year,
            "regime":           _REGIME_NOTES.get(year, ""),
            "skipped":          False,
            "n_trades":         len(window_trades),
            "win_rate":         round(len(wins) / len(window_trades) * 100, 1),
            "profit_factor":    round(pf, 3),
            "net_pct":          round(net_pct, 2),
            "max_drawdown_pct": round(max_dd, 2),
        })

    return results


def _print_table(instrument: str, rows: list[dict[str, Any]]) -> None:
    valid = [r for r in rows if not r.get("skipped")]

    print(f"\n{'=' * 75}")
    print(f"London Trend Walk-Forward — {instrument}  ({len(valid)} annual windows)")
    print(f"{'=' * 75}")
    print(f"  {'Year':<6}  {'Trades':>6}  {'WR%':>6}  {'PF':>5}  {'Net%':>6}  {'MaxDD%':>7}  Notes")
    print(f"  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*5}  {'-'*6}  {'-'*7}  {'-'*30}")

    for r in rows:
        if r.get("skipped"):
            print(f"  {r['year']:<6}  {'—':>6}  {'—':>6}  {'—':>5}  {'—':>6}  {'—':>7}  {r.get('reason','')} ({r.get('regime','')})")
            continue
        pf_str  = f"{r['profit_factor']:.2f}" if r["profit_factor"] != float("inf") else "∞"
        net_str = f"{r['net_pct']:+.1f}%"
        flag    = "✓" if r["profit_factor"] > 1.0 else "✗"
        print(f"  {r['year']:<6}  {r['n_trades']:>6}  {r['win_rate']:>5.1f}%  "
              f"{pf_str:>5}  {net_str:>6}  {r['max_drawdown_pct']:>6.1f}%  {flag}  {r['regime']}")

    if not valid:
        print("\n  No valid windows — check CSV date range.\n")
        return

    profitable = [r for r in valid if r["profit_factor"] > 1.0]
    avg_pf     = sum(r["profit_factor"] for r in valid) / len(valid)
    avg_wr     = sum(r["win_rate"] for r in valid) / len(valid)
    avg_net    = sum(r["net_pct"]  for r in valid) / len(valid)
    worst_dd   = min(r["max_drawdown_pct"] for r in valid)

    print(f"\n  {'─'*60}")
    print(f"  Profitable years (PF > 1.0):  {len(profitable)}/{len(valid)} = {len(profitable)/len(valid)*100:.0f}%")
    print(f"  Average PF:                   {avg_pf:.3f}")
    print(f"  Average win rate:             {avg_wr:.1f}%")
    print(f"  Average annual net return:    {avg_net:+.1f}%")
    print(f"  Worst single-year drawdown:   {worst_dd:.1f}%")

    consistent = len(profitable) / len(valid) >= 0.75
    strong     = avg_pf >= 1.3 and avg_wr >= 44.0
    if consistent and strong:
        verdict = "STRONG EDGE — consistent across regimes"
    elif consistent:
        verdict = "ACCEPTABLE EDGE — mostly consistent, monitor live"
    elif len(profitable) / len(valid) >= 0.57:
        verdict = "MARGINAL EDGE — profitable in most windows, review losing years"
    else:
        verdict = "WEAK EDGE — fails too many windows"
    print(f"  Verdict: {verdict}")
    print(f"{'=' * 75}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="London trend strategy walk-forward validation")
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--h1-csv",  required=True)
    parser.add_argument("--h4-csv",  default=None)
    parser.add_argument("--d-csv",   default=None)
    parser.add_argument("--balance", type=float, default=10_000.0)
    args = parser.parse_args()

    df_h1 = _load_csv(args.h1_csv)
    df_h4 = _load_csv(args.h4_csv) if args.h4_csv and Path(args.h4_csv).exists() else None
    df_d  = _load_csv(args.d_csv)  if args.d_csv  and Path(args.d_csv).exists()  else None

    rows = run_walk_forward(args.instrument, df_h1, df_h4, df_d, initial_balance=args.balance)
    _print_table(args.instrument, rows)


if __name__ == "__main__":
    main()
