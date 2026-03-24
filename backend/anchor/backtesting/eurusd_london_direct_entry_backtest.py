"""EUR_USD London trend direct-entry rescue backtest.

Purpose:
- isolate the only serious London trend rescue candidate
- replace pullback-limit execution with immediate entry on the signal bar
- report yearly stability so the result can be promoted or killed quickly
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from anchor.backtesting.london_rescue_matrix import _MarketEntryBacktestEngine

logging.disable(logging.CRITICAL)


def _load_df(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def _slice_with_warmup(df: pd.DataFrame, start: str | None, end: str | None, warmup: int) -> pd.DataFrame:
    out = df
    if start:
        start_ts = pd.Timestamp(start, tz="UTC")
        pre = df[df["time"] < start_ts].tail(warmup)
        out = pd.concat([pre, df[df["time"] >= start_ts]], ignore_index=True)
    if end:
        end_ts = pd.Timestamp(end, tz="UTC")
        out = out[out["time"] <= end_ts]
    return out.reset_index(drop=True)


def _run_window(
    pair: str,
    data_dir: Path,
    balance: float,
    threshold: float,
    start: str | None,
    end: str | None,
    no_hmm: bool,
):
    h1 = _slice_with_warmup(_load_df(data_dir / f"{pair}_H1.csv"), start, end, 200)
    h4 = _slice_with_warmup(_load_df(data_dir / f"{pair}_H4.csv"), start, end, 100)
    d1 = _slice_with_warmup(_load_df(data_dir / f"{pair}_D.csv"), start, end, 365)

    flags = {"ablation_hmm_gate": False} if no_hmm else {}
    eng = _MarketEntryBacktestEngine(
        initial_balance=balance,
        confluence_threshold=threshold,
        ablation_flags=flags,
    )
    eng.load_df(pair, "H1", h1)
    eng.load_df(pair, "H4", h4)
    eng.load_df(pair, "D", d1)
    return eng.run(pair, "H1")


def _yearly_rows(trade_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not trade_log:
        return []
    df = pd.DataFrame(trade_log)
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    rows: list[dict[str, Any]] = []
    for year, g in df.groupby(df["entry_time"].dt.year):
        pnls = g["net_pl"].astype(float)
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        pf = float(wins.sum() / abs(losses.sum())) if len(losses) and abs(losses.sum()) > 1e-12 else float("inf")
        wr = float((pnls > 0).mean() * 100.0)
        rows.append(
            {
                "year": str(year),
                "trades": int(len(g)),
                "win_rate": wr,
                "profit_factor": pf,
                "net_pl": float(pnls.sum()),
                "mean_pl": float(pnls.mean()),
            }
        )
    return rows


def _print(results, threshold: float, no_hmm: bool) -> None:
    print(f"\n{'=' * 84}")
    print("EUR_USD LONDON DIRECT ENTRY RESCUE")
    print(f"{'=' * 84}")
    print(f"Threshold : {threshold:.2f}")
    print(f"HMM gate  : {'OFF' if no_hmm else 'ON'}")
    print(f"Trades    : {results.total_trades}")
    print(f"WR        : {results.win_rate * 100:.1f}%")
    print(f"PF        : {'inf' if results.profit_factor == float('inf') else f'{results.profit_factor:.3f}'}")
    print(f"Net%      : {results.net_pnl_pct:+.2f}%")
    print(f"MaxDD%    : {results.max_drawdown_pct:.2f}%")
    print(f"Sharpe    : {results.sharpe_ratio:.2f}")
    print(f"Sortino   : {results.sortino_ratio:.2f}")
    print(f"Avg Win$  : {results.avg_win_pips:.2f}")
    print(f"Avg Loss$ : {results.avg_loss_pips:.2f}")

    yearly = _yearly_rows(results.trade_log)
    if yearly:
        print(f"\n{'Year':<6} {'Trades':>7} {'WR':>7} {'PF':>8} {'Net$':>10} {'Mean$':>10}")
        for row in yearly:
            pf_str = "inf" if row["profit_factor"] == float("inf") else f"{row['profit_factor']:.3f}"
            print(
                f"{row['year']:<6} {row['trades']:>7} {row['win_rate']:>6.1f}% "
                f"{pf_str:>8} {row['net_pl']:>+9.2f} {row['mean_pl']:>+9.2f}"
            )
    print(f"{'=' * 84}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="EUR_USD London trend direct-entry rescue backtest")
    parser.add_argument("--pair", default="EUR_USD")
    parser.add_argument("--data-dir", default="/app/data")
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--no-hmm", action="store_true")
    args = parser.parse_args()

    results = _run_window(
        pair=args.pair,
        data_dir=Path(args.data_dir),
        balance=args.balance,
        threshold=args.threshold,
        start=args.start,
        end=args.end,
        no_hmm=args.no_hmm,
    )
    _print(results, threshold=args.threshold, no_hmm=args.no_hmm)


if __name__ == "__main__":
    main()
