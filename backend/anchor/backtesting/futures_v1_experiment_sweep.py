"""Robustness sweep for Anchor Futures v1."""
from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import pandas as pd

from anchor.backtesting.futures_v1_backtest import run_futures_v1_backtest


def _combo_name(
    markets: list[str],
    trend_lookback_days: int,
    rebalance_frequency: str,
    threshold: float,
) -> str:
    label = ",".join(markets)
    return f"{label}|lb{trend_lookback_days}|{rebalance_frequency}|th{threshold:.3f}"


def _market_sets(base_markets: list[str]) -> list[list[str]]:
    sets: list[list[str]] = [base_markets]
    for market in base_markets:
        subset = [item for item in base_markets if item != market]
        if len(subset) >= 3:
            sets.append(subset)
    return sets


def main() -> None:
    parser = argparse.ArgumentParser(description="Anchor Futures v1 robustness sweep")
    parser.add_argument("--data-dir", default="/app/data")
    parser.add_argument("--markets", default="MES,MNQ,ZN,MGC,MCL")
    parser.add_argument("--balance", type=float, default=100000.0)
    parser.add_argument("--export-csv", default="/app/data/futures_v1_sweep.csv")
    parser.add_argument("--export-json", default="/app/data/futures_v1_sweep.json")
    args = parser.parse_args()

    base_markets = [part.strip().upper() for part in args.markets.split(",") if part.strip()]
    lookbacks = [63, 126, 189, 252]
    rebalance_frequencies = ["weekly", "monthly"]
    thresholds = [0.0, 0.01, 0.02, 0.03]

    rows = []
    details = {}

    for markets in _market_sets(base_markets):
        for lookback in lookbacks:
            for rebalance_frequency in rebalance_frequencies:
                for threshold in thresholds:
                    result, _ = run_futures_v1_backtest(
                        data_dir=args.data_dir,
                        markets=markets,
                        initial_balance=args.balance,
                        trend_lookback_days=lookback,
                        vol_lookback_days=20,
                        rebalance_frequency=rebalance_frequency,
                        threshold=threshold,
                    )
                    key = _combo_name(markets, lookback, rebalance_frequency, threshold)
                    details[key] = result
                    metrics = result["metrics"]
                    rows.append(
                        {
                            "combo": key,
                            "markets": ",".join(markets),
                            "market_count": len(markets),
                            "missing_market": next((market for market in base_markets if market not in markets), ""),
                            "trend_lookback_days": lookback,
                            "rebalance_frequency": rebalance_frequency,
                            "threshold": threshold,
                            **metrics,
                        }
                    )

    frame = pd.DataFrame(rows).sort_values(
        ["sharpe", "annualized_return_pct", "max_drawdown_pct"],
        ascending=[False, False, False],
    )
    Path(args.export_csv).parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.export_csv, index=False)
    Path(args.export_json).write_text(json.dumps(details, indent=2) + "\n", encoding="utf-8")
    print(frame.to_string(index=False))


if __name__ == "__main__":
    main()
