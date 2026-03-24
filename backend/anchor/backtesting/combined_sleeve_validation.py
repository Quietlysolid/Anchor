"""Combined London Trend + LCR portfolio validation.

Runs both sleeves across their configured pair universes, merges trade logs
into a single fixed-risk return stream, and reports:

- per-sleeve / per-pair trade quality
- combined portfolio equity and drawdown
- trade overlap / concurrency statistics
- bootstrap confidence intervals
- sign-flip test
- forward Monte Carlo on the merged sleeve

The return stream is normalized onto a common fractional-return basis:
- London Trend: net_pl / initial_balance
- LCR: stored pl_pct / 100

This is intentionally approximate, but it is materially better than treating
incompatible absolute PnL units as directly comparable.
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from anchor.backtesting.engine import BacktestEngine
from anchor.backtesting.lcr_backtest import LCRBacktestEngine, _load_csv as _load_lcr_csv
from anchor.backtesting.validation_utils import (
    bootstrap_confidence_intervals,
    forward_equity_simulation,
    sign_flip_test,
)
from anchor.config import settings
from anchor.signals.london_close_reversion import LCR_INSTRUMENTS

logging.disable(logging.CRITICAL)

DATA_DIR = Path("/app/data")


@dataclass
class SleeveRun:
    sleeve: str
    pair: str
    trades: list[dict[str, Any]]
    stats: dict[str, Any]


def _load_df(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def _split_csv_list(value: str | None, fallback: list[str]) -> list[str]:
    if not value:
        return fallback
    return [item.strip() for item in value.split(",") if item.strip()]


def _return_metrics(returns: np.ndarray, initial_balance: float) -> dict[str, float]:
    arr = np.asarray(returns, dtype=float)
    if arr.size == 0:
        return {
            "n_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "net_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "mean_bps": 0.0,
            "final_balance": initial_balance,
        }

    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    pf = float(wins.sum() / abs(losses.sum())) if losses.size > 0 and abs(losses.sum()) > 1e-12 else float("inf")

    equity = initial_balance * np.cumprod(np.concatenate([[1.0], 1.0 + arr]))
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / np.maximum(peak, 1e-12)
    max_dd = float(np.min(dd)) * 100.0

    return {
        "n_trades": int(arr.size),
        "win_rate": float(np.mean(arr > 0) * 100.0),
        "profit_factor": pf,
        "net_pct": float((equity[-1] / initial_balance - 1.0) * 100.0),
        "max_drawdown_pct": max_dd,
        "mean_bps": float(np.mean(arr) * 10_000.0),
        "final_balance": float(equity[-1]),
    }


def _trade_concurrency_stats(df: pd.DataFrame) -> dict[str, float]:
    if df.empty:
        return {
            "max_concurrent": 0.0,
            "avg_concurrent": 0.0,
            "overlap_trade_pct": 0.0,
        }

    rows = df.sort_values(["entry_time", "exit_time"]).to_dict("records")
    open_ends: list[pd.Timestamp] = []
    concurrency_points: list[int] = []
    overlap_count = 0

    for row in rows:
        entry = row["entry_time"]
        exit_ = row["exit_time"]
        open_ends = [ts for ts in open_ends if ts > entry]
        if open_ends:
            overlap_count += 1
        open_ends.append(exit_)
        concurrency_points.append(len(open_ends))

    return {
        "max_concurrent": float(max(concurrency_points) if concurrency_points else 0),
        "avg_concurrent": float(np.mean(concurrency_points) if concurrency_points else 0.0),
        "overlap_trade_pct": float(overlap_count / len(rows) * 100.0),
    }


def _yearly_summary(df: pd.DataFrame, initial_balance: float) -> list[dict[str, Any]]:
    if df.empty:
        return []

    rows: list[dict[str, Any]] = []
    for year, g in df.groupby(df["entry_time"].dt.year):
        metrics = _return_metrics(g["ret_frac"].to_numpy(dtype=float), initial_balance)
        rows.append(
            {
                "year": str(year),
                "n_trades": metrics["n_trades"],
                "win_rate": metrics["win_rate"],
                "profit_factor": metrics["profit_factor"],
                "net_pct": metrics["net_pct"],
                "max_drawdown_pct": metrics["max_drawdown_pct"],
                "mean_bps": metrics["mean_bps"],
            }
        )
    return rows


def _run_trend_pair(
    pair: str,
    data_dir: Path,
    initial_balance: float,
    start: str | None,
    end: str | None,
) -> SleeveRun | None:
    h1 = data_dir / f"{pair}_H1.csv"
    h4 = data_dir / f"{pair}_H4.csv"
    d1 = data_dir / f"{pair}_D.csv"
    if not (h1.exists() and h4.exists() and d1.exists()):
        return None

    df_h1 = _load_df(h1)
    df_h4 = _load_df(h4)
    df_d1 = _load_df(d1)

    if start:
        start_ts = pd.Timestamp(start, tz="UTC")
        pre_h1 = df_h1[df_h1["time"] < start_ts].tail(200)
        df_h1 = pd.concat([pre_h1, df_h1[df_h1["time"] >= start_ts]], ignore_index=True)
        df_h4 = df_h4[df_h4["time"] >= start_ts - pd.Timedelta(days=120)]
        df_d1 = df_d1[df_d1["time"] >= start_ts - pd.Timedelta(days=365)]
    if end:
        end_ts = pd.Timestamp(end, tz="UTC")
        df_h1 = df_h1[df_h1["time"] <= end_ts]
        df_h4 = df_h4[df_h4["time"] <= end_ts]
        df_d1 = df_d1[df_d1["time"] <= end_ts]

    engine = BacktestEngine(initial_balance=initial_balance)
    engine.load_df(pair, "H1", df_h1)
    engine.load_df(pair, "H4", df_h4)
    engine.load_df(pair, "D", df_d1)
    results = engine.run(pair, "H1")

    trades: list[dict[str, Any]] = []
    for trade in results.trade_log:
        entry_time = pd.Timestamp(trade["entry_time"], tz="UTC")
        exit_time = pd.Timestamp(trade["exit_time"], tz="UTC")
        if start and entry_time < pd.Timestamp(start, tz="UTC"):
            continue
        if end and entry_time > pd.Timestamp(end, tz="UTC"):
            continue
        ret_frac = float(trade["net_pl"]) / float(initial_balance)
        trades.append(
            {
                "sleeve": "LONDON",
                "pair": pair,
                "entry_time": entry_time,
                "exit_time": exit_time,
                "ret_frac": ret_frac,
                "pnl_unit": float(trade["net_pl"]),
                "direction": trade.get("direction"),
                "session": trade.get("session"),
                "close_reason": trade.get("close_reason"),
            }
        )

    stats = _return_metrics(np.array([t["ret_frac"] for t in trades], dtype=float), initial_balance)
    return SleeveRun(sleeve="LONDON", pair=pair, trades=trades, stats=stats)


def _run_lcr_pair(
    pair: str,
    data_dir: Path,
    initial_balance: float,
    start: str | None,
    end: str | None,
    spread_mult: float,
    slippage_pips: float,
) -> SleeveRun | None:
    h1 = data_dir / f"{pair}_H1.csv"
    if not h1.exists():
        return None

    df_h1 = _load_lcr_csv(str(h1))
    engine = LCRBacktestEngine(initial_balance=initial_balance)
    results = engine.run(
        instrument=pair,
        df_h1=df_h1,
        start=start,
        end=end,
        spread_mult=spread_mult,
        slippage_pips=slippage_pips,
    )
    trades_src = results.get("trades", [])
    trades: list[dict[str, Any]] = []
    for trade in trades_src:
        ret_frac = float(trade["pl_pct"]) / 100.0
        trades.append(
            {
                "sleeve": "LCR",
                "pair": pair,
                "entry_time": pd.Timestamp(trade["entry_time"], tz="UTC"),
                "exit_time": pd.Timestamp(trade["exit_time"], tz="UTC"),
                "ret_frac": ret_frac,
                "pnl_unit": float(trade["pl_pips"]),
                "direction": trade.get("direction"),
                "session": "NY_LCR",
                "close_reason": trade.get("reason"),
            }
        )

    stats = _return_metrics(np.array([t["ret_frac"] for t in trades], dtype=float), initial_balance)
    return SleeveRun(sleeve="LCR", pair=pair, trades=trades, stats=stats)


def run_combined_validation(
    trend_pairs: list[str],
    lcr_pairs: list[str],
    data_dir: Path,
    initial_balance: float,
    start: str | None,
    end: str | None,
    spread_mult: float,
    slippage_pips: float,
) -> dict[str, Any]:
    runs: list[SleeveRun] = []

    for pair in trend_pairs:
        run = _run_trend_pair(pair, data_dir, initial_balance, start, end)
        if run is not None and run.trades:
            runs.append(run)

    for pair in lcr_pairs:
        run = _run_lcr_pair(pair, data_dir, initial_balance, start, end, spread_mult, slippage_pips)
        if run is not None and run.trades:
            runs.append(run)

    all_trades = [trade for run in runs for trade in run.trades]
    if not all_trades:
        return {"error": "No trades generated"}

    df = pd.DataFrame(all_trades).sort_values(["entry_time", "sleeve", "pair"]).reset_index(drop=True)
    combined_returns = df["ret_frac"].to_numpy(dtype=float)
    pnl_bps = combined_returns * 10_000.0

    sleeve_rows = []
    for sleeve, g in df.groupby("sleeve"):
        metrics = _return_metrics(g["ret_frac"].to_numpy(dtype=float), initial_balance)
        sleeve_rows.append({"sleeve": sleeve, **metrics})

    pair_rows = []
    for (sleeve, pair), g in df.groupby(["sleeve", "pair"]):
        metrics = _return_metrics(g["ret_frac"].to_numpy(dtype=float), initial_balance)
        pair_rows.append({"sleeve": sleeve, "pair": pair, **metrics})

    overlap = _trade_concurrency_stats(df)
    combined_metrics = _return_metrics(combined_returns, initial_balance)
    yearly = _yearly_summary(df, initial_balance)
    boot = bootstrap_confidence_intervals(pnl_bps, n_boot=1000, method="block", block_size=5)
    sign = sign_flip_test(pnl_bps, n_perm=5000)
    entry_months = df["entry_time"].dt.tz_localize(None).dt.to_period("M")
    sim = forward_equity_simulation(
        pnl_bps,
        initial_balance=initial_balance,
        months=6,
        trades_per_month=max(1, int(round(len(df) / max(1.0, entry_months.nunique())))),
        n_sim=500,
        block_size=5,
    )

    monthly = (
        df.assign(month=df["entry_time"].dt.tz_localize(None).dt.to_period("M").astype(str))
        .pivot_table(index="month", columns="sleeve", values="ret_frac", aggfunc="sum", fill_value=0.0)
    )
    sleeve_corr = monthly.corr() if len(monthly) >= 3 and monthly.shape[1] >= 2 else None

    return {
        "sleeve_rows": sleeve_rows,
        "pair_rows": pair_rows,
        "combined": combined_metrics,
        "overlap": overlap,
        "yearly": yearly,
        "bootstrap": boot,
        "sign_flip": sign,
        "forward_sim": sim,
        "sleeve_corr": sleeve_corr,
        "trade_count": len(df),
        "trend_pairs": trend_pairs,
        "lcr_pairs": lcr_pairs,
    }


def _print_results(result: dict[str, Any]) -> None:
    if result.get("error"):
        print(f"ERROR: {result['error']}")
        return

    print(f"\n{'=' * 96}")
    print("COMBINED LONDON + LCR VALIDATION")
    print(f"{'=' * 96}")
    print(f"Trend pairs : {', '.join(result['trend_pairs'])}")
    print(f"LCR pairs   : {', '.join(result['lcr_pairs'])}")
    print(f"Trades      : {result['trade_count']}")

    print(f"\n{'-' * 96}")
    print("BY SLEEVE")
    print(f"{'Sleeve':<10} {'N':>6} {'WR':>7} {'PF':>8} {'Net%':>9} {'MaxDD%':>9} {'Mean bps':>10}")
    for row in sorted(result["sleeve_rows"], key=lambda r: r["sleeve"]):
        pf = row["profit_factor"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.3f}"
        print(
            f"{row['sleeve']:<10} {row['n_trades']:>6} {row['win_rate']:>6.1f}% {pf_str:>8} "
            f"{row['net_pct']:>+8.1f}% {row['max_drawdown_pct']:>8.1f}% {row['mean_bps']:>+9.1f}"
        )

    print(f"\n{'-' * 96}")
    print("BY PAIR")
    print(f"{'Sleeve':<10} {'Pair':<10} {'N':>6} {'WR':>7} {'PF':>8} {'Net%':>9} {'MaxDD%':>9} {'Mean bps':>10}")
    for row in sorted(result["pair_rows"], key=lambda r: (r["sleeve"], r["pair"])):
        pf = row["profit_factor"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.3f}"
        print(
            f"{row['sleeve']:<10} {row['pair']:<10} {row['n_trades']:>6} {row['win_rate']:>6.1f}% {pf_str:>8} "
            f"{row['net_pct']:>+8.1f}% {row['max_drawdown_pct']:>8.1f}% {row['mean_bps']:>+9.1f}"
        )

    combined = result["combined"]
    combined_pf = combined["profit_factor"]
    combined_pf_str = "inf" if combined_pf == float("inf") else f"{combined_pf:.3f}"
    print(f"\n{'-' * 96}")
    print("COMBINED PORTFOLIO")
    print(f"WR         : {combined['win_rate']:.1f}%")
    print(f"PF         : {combined_pf_str}")
    print(f"Net%       : {combined['net_pct']:+.1f}%")
    print(f"MaxDD      : {combined['max_drawdown_pct']:.1f}%")
    print(f"Mean trade : {combined['mean_bps']:+.1f} bps")

    overlap = result["overlap"]
    print(f"\nOVERLAP")
    print(f"Max concurrent   : {overlap['max_concurrent']:.0f}")
    print(f"Avg concurrent   : {overlap['avg_concurrent']:.2f}")
    print(f"Overlap trade %  : {overlap['overlap_trade_pct']:.1f}%")

    boot = result["bootstrap"]
    if boot:
        wr = boot.get("wr", {})
        pf = boot.get("pf", {})
        dd = boot.get("maxdd", {})
        print(f"\nBOOTSTRAP (90% block CI)")
        print(f"WR CI      : {wr.get('lo', 0.0) * 100:.1f}% - {wr.get('hi', 0.0) * 100:.1f}%")
        print(f"PF CI      : {pf.get('lo', 0.0):.3f} - {pf.get('hi', 0.0):.3f}")
        print(f"MaxDD CI   : {dd.get('lo', 0.0) * 100:.1f}% - {dd.get('hi', 0.0) * 100:.1f}%")

    sign = result["sign_flip"]
    print(f"\nSIGN-FLIP TEST")
    print(f"Mean trade : {sign['actual_mean_pl']:+.2f} bps")
    print(f"z-score    : {sign['z_score']:+.2f}")
    print(f"p-value    : {sign['p_value']:.4f}")

    sim = result["forward_sim"]
    print(f"\nFORWARD MONTE CARLO (6m)")
    print(f"Median      : {sim['median_return']:+.1f}%")
    print(f"P5 / P95    : {sim['p5_return']:+.1f}% / {sim['p95_return']:+.1f}%")
    print(f"Prob loss   : {sim['prob_loss']:.1f}%")
    print(f"Prob ruin   : {sim['prob_ruin']:.1f}%")

    yearly = result["yearly"]
    if yearly:
        print(f"\nYEARLY STABILITY")
        print(f"{'Year':<6} {'N':>6} {'WR':>7} {'PF':>8} {'Net%':>9} {'MaxDD%':>9} {'Mean bps':>10}")
        for row in yearly:
            pf = row["profit_factor"]
            pf_str = "inf" if pf == float("inf") else f"{pf:.3f}"
            print(
                f"{row['year']:<6} {row['n_trades']:>6} {row['win_rate']:>6.1f}% {pf_str:>8} "
                f"{row['net_pct']:>+8.1f}% {row['max_drawdown_pct']:>8.1f}% {row['mean_bps']:>+9.1f}"
            )

    corr = result.get("sleeve_corr")
    if corr is not None:
        print(f"\nMONTHLY SLEEVE CORRELATION")
        print(corr.to_string(float_format=lambda x: f"{x:0.3f}"))

    print(f"{'=' * 96}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Combined London Trend + LCR portfolio validation")
    parser.add_argument("--trend-pairs", default=",".join(settings.trend_instruments))
    parser.add_argument("--lcr-pairs", default=",".join(sorted(LCR_INSTRUMENTS)))
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--spread-mult", type=float, default=1.0)
    parser.add_argument("--slippage-pips", type=float, default=0.0)
    args = parser.parse_args()

    result = run_combined_validation(
        trend_pairs=_split_csv_list(args.trend_pairs, settings.trend_instruments),
        lcr_pairs=_split_csv_list(args.lcr_pairs, sorted(LCR_INSTRUMENTS)),
        data_dir=Path(args.data_dir),
        initial_balance=args.balance,
        start=args.start,
        end=args.end,
        spread_mult=args.spread_mult,
        slippage_pips=args.slippage_pips,
    )
    _print_results(result)


if __name__ == "__main__":
    main()
