"""Walk-forward backtest runner.

Splits a CSV dataset into three non-overlapping windows:
  train  — used to fit HMM / ML (not backtested; just data context)
  val    — tune thresholds / sanity check
  oos    — out-of-sample holdout, NEVER touched until final evaluation

Each window is run through BacktestEngine independently and results are
printed side-by-side so degradation from train→val→oos is immediately visible.

Usage:
    python -m anchor.backtesting.walk_forward_backtest \\
        --instrument EUR_USD \\
        --h1-csv  data/EURUSD_H1.csv \\
        --h4-csv  data/EURUSD_H4.csv \\
        --d-csv   data/EURUSD_D.csv \\
        --train-end  2021-12-31 \\
        --val-end    2023-12-31 \\
        --balance    10000
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from anchor.backtesting.engine import BacktestEngine
from anchor.backtesting.results import BacktestResults


def _utc(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _slice(df: pd.DataFrame, start: datetime | None, end: datetime | None) -> pd.DataFrame:
    mask = pd.Series([True] * len(df), index=df.index)
    if start:
        mask &= df["time"] >= start
    if end:
        mask &= df["time"] <= end
    return df[mask].reset_index(drop=True)


@dataclass
class WindowResult:
    label: str
    start: str
    end: str
    results: BacktestResults


def run_window(
    label: str,
    instrument: str,
    h1: pd.DataFrame,
    h4: pd.DataFrame | None,
    d1: pd.DataFrame | None,
    balance: float,
) -> WindowResult:
    eng = BacktestEngine(initial_balance=balance)
    eng.load_df(instrument, "H1", h1)
    if h4 is not None and len(h4) > 0:
        eng.load_df(instrument, "H4", h4)
    if d1 is not None and len(d1) > 0:
        eng.load_df(instrument, "D", d1)

    results = eng.run(instrument, "H1")
    start = str(h1["time"].min().date()) if len(h1) else "—"
    end   = str(h1["time"].max().date()) if len(h1) else "—"
    return WindowResult(label=label, start=start, end=end, results=results)


def print_comparison(windows: list[WindowResult]) -> None:
    W = "\033[92m"
    L = "\033[91m"
    N = "\033[0m"
    H = "\033[1m"

    def _c(val: float, good_if_positive: bool = True) -> str:
        col = W if (val > 0) == good_if_positive else L
        return f"{col}{val}{N}"

    col_w = 18
    header = f"{'Metric':<26}" + "".join(f"{w.label:>{col_w}}" for w in windows)
    print(f"\n{'='*70}")
    print(f"{H}WALK-FORWARD BACKTEST COMPARISON{N}")
    print(f"{'='*70}")
    print(f"{H}{header}{N}")
    print("-" * 70)

    def row(name: str, vals: list, fmt: str = ".2f", good_positive: bool = True) -> None:
        cells = ""
        for v in vals:
            formatted = f"{v:{fmt}}"
            col = W if (v > 0) == good_positive else L
            cells += f"{col}{formatted:>{col_w}}{N}"
        print(f"  {name:<24}{cells}")

    dates = f"{'Period':<26}" + "".join(f"{w.start + '→' + w.end:>{col_w}}" for w in windows)
    print(f"  {dates}")
    print()

    trades   = [w.results.total_trades for w in windows]
    wr       = [round(w.results.win_rate * 100, 1) for w in windows]
    pf       = [w.results.profit_factor for w in windows]
    pnl_pct  = [w.results.net_pnl_pct for w in windows]
    maxdd    = [w.results.max_drawdown_pct for w in windows]
    sharpe   = [w.results.sharpe_ratio for w in windows]
    sortino  = [w.results.sortino_ratio for w in windows]
    avg_dur  = [w.results.avg_trade_duration_hours for w in windows]

    # trades — just show, no colour
    cells = "".join(f"{v:>{col_w}}" for v in trades)
    print(f"  {'Total Trades':<24}{cells}")

    row("Win Rate (%)",        wr,      ".1f")
    row("Profit Factor",       pf,      ".2f")
    row("Net P&L (%)",         pnl_pct, ".2f")
    row("Max Drawdown (%)",    maxdd,   ".2f", good_positive=False)
    row("Sharpe Ratio",        sharpe,  ".2f")
    row("Sortino Ratio",       sortino, ".2f")
    row("Avg Trade Duration",  avg_dur, ".1f")

    print(f"\n{'='*70}")

    # Gate check
    print(f"\n{H}GATE CHECK  (OOS window must pass ALL to proceed to live){N}")
    oos = windows[-1].results
    gates = [
        ("Trades ≥ 200",       oos.total_trades >= 200,          str(oos.total_trades)),
        ("Sharpe > 1.0",       oos.sharpe_ratio > 1.0,           f"{oos.sharpe_ratio:.2f}"),
        ("Max DD < 20%",       oos.max_drawdown_pct < 20.0,      f"{oos.max_drawdown_pct:.1f}%"),
        ("Profit Factor > 1.3",oos.profit_factor > 1.3,          f"{oos.profit_factor:.2f}"),
        ("Win Rate > 45%",     oos.win_rate > 0.45,              f"{oos.win_rate*100:.1f}%"),
    ]
    all_pass = True
    for name, passed, val in gates:
        icon = f"{W}✓{N}" if passed else f"{L}✗{N}"
        all_pass = all_pass and passed
        print(f"  {icon}  {name:<30} {val}")

    if all_pass:
        print(f"\n  {W}{H}ALL GATES PASSED — system may proceed to paper trading.{N}")
    else:
        print(f"\n  {L}{H}GATES FAILED — do not deploy. Investigate signal design.{N}")
    print()


def _main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward backtest")
    parser.add_argument("--instrument",  default="EUR_USD")
    parser.add_argument("--h1-csv",      required=True)
    parser.add_argument("--h4-csv",      default=None)
    parser.add_argument("--d-csv",       default=None)
    parser.add_argument("--train-end",   default="2021-12-31",
                        help="End of training window (YYYY-MM-DD)")
    parser.add_argument("--val-end",     default="2023-12-31",
                        help="End of validation window (YYYY-MM-DD)")
    parser.add_argument("--balance",     type=float, default=10_000.0)
    args = parser.parse_args()

    train_end = _utc(args.train_end)
    val_end   = _utc(args.val_end)

    def load(path: str | None) -> pd.DataFrame | None:
        if path is None:
            return None
        df = pd.read_csv(path, parse_dates=["time"])
        df["time"] = pd.to_datetime(df["time"], utc=True)
        return df.sort_values("time").reset_index(drop=True)

    h1 = load(args.h1_csv)
    h4 = load(args.h4_csv)
    d1 = load(args.d_csv)

    windows_out: list[WindowResult] = []

    for label, start, end in [
        ("TRAIN",      None,       train_end),
        ("VALIDATION", train_end,  val_end),
        ("OOS",        val_end,    None),
    ]:
        h1_w = _slice(h1, start, end)
        h4_w = _slice(h4, start, end) if h4 is not None else None
        d1_w = _slice(d1, start, end) if d1 is not None else None

        if len(h1_w) < 200:
            print(f"  {label}: skipped (< 200 H1 bars in window)")
            continue

        print(f"\nRunning {label} window ({len(h1_w)} H1 bars)…")
        w = run_window(label, args.instrument, h1_w, h4_w, d1_w, args.balance)
        windows_out.append(w)

    if windows_out:
        print_comparison(windows_out)


if __name__ == "__main__":
    _main()
