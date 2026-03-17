"""
Walk-Forward Validation for LCR Strategy.

Tests whether the LCR edge is consistent across time by running independent
1-year OOS windows from 2016 to 2024. Each window is completely independent —
no look-ahead, no parameter fitting per window.

If the edge is real, the majority of windows should show PF > 1.0 and positive
net returns. Clustering of losses in a single year reveals regime sensitivity.

Usage:
    python -m anchor.backtesting.walk_forward \\
        --instrument EUR_USD \\
        --h1-csv data/EUR_USD_H1.csv

    # All 3 LCR pairs
    for pair in EUR_USD GBP_USD USD_JPY; do
        python -m anchor.backtesting.walk_forward \\
            --instrument $pair \\
            --h1-csv data/${pair}_H1.csv
    done
"""
from __future__ import annotations

import argparse
from typing import Any

import pandas as pd

from anchor.backtesting.lcr_backtest import LCRBacktestEngine, _load_csv, _SPREAD_COST


# Annual OOS windows (inclusive). Adjust end year if data ends earlier.
_WINDOWS: list[tuple[str, str]] = [
    ("2016-01-01", "2016-12-31"),
    ("2017-01-01", "2017-12-31"),
    ("2018-01-01", "2018-12-31"),
    ("2019-01-01", "2019-12-31"),
    ("2020-01-01", "2020-12-31"),
    ("2021-01-01", "2021-12-31"),
    ("2022-01-01", "2022-12-31"),
    ("2023-01-01", "2023-12-31"),
    ("2024-01-01", "2024-12-31"),
]

# Notable macro regimes to annotate results (rough characterisation)
_REGIME_NOTES: dict[str, str] = {
    "2016": "Brexit / Trump election volatility",
    "2017": "Low vol, slow USD trend",
    "2018": "Fed hike cycle, USD strength",
    "2019": "Trade war, late-cycle ranging",
    "2020": "COVID — extreme vol / regime breaks",
    "2021": "Recovery, low vol range expansion",
    "2022": "Inflation shock, massive trend moves",
    "2023": "Banking stress, reversion to range",
    "2024": "Rate-cut anticipation, mixed",
}


def run_walk_forward(
    instrument: str,
    df_h1: pd.DataFrame,
    initial_balance: float = 10_000.0,
) -> list[dict[str, Any]]:
    """Run walk-forward across all defined annual windows.

    Returns list of per-window result dicts.
    """
    engine  = LCRBacktestEngine(initial_balance=initial_balance)
    results = []

    for start, end in _WINDOWS:
        year = start[:4]

        # Check if we have enough data in this window
        window_data = df_h1[
            (df_h1.index >= pd.Timestamp(start, tz="UTC")) &
            (df_h1.index <= pd.Timestamp(end, tz="UTC"))
        ]
        if len(window_data) < 50:
            results.append({
                "year":    year,
                "regime":  _REGIME_NOTES.get(year, ""),
                "skipped": True,
                "reason":  f"only {len(window_data)} bars",
            })
            continue

        res = engine.run(instrument=instrument, df_h1=df_h1, start=start, end=end)
        stats = res.get("stats", {})

        if stats.get("n_trades", 0) == 0:
            results.append({
                "year":    year,
                "regime":  _REGIME_NOTES.get(year, ""),
                "skipped": True,
                "reason":  "no trades fired",
            })
            continue

        results.append({
            "year":             year,
            "regime":           _REGIME_NOTES.get(year, ""),
            "skipped":          False,
            "n_trades":         stats["n_trades"],
            "win_rate":         stats["win_rate"],
            "profit_factor":    stats["profit_factor"],
            "net_pct":          stats["net_pct"],
            "max_drawdown_pct": stats["max_drawdown_pct"],
            "trades_per_month": stats.get("trades_per_month", 0),
            "avg_win_pips":     stats.get("avg_win_pips", 0),
            "avg_loss_pips":    stats.get("avg_loss_pips", 0),
        })

    return results


def _print_table(instrument: str, rows: list[dict[str, Any]]) -> None:
    valid = [r for r in rows if not r.get("skipped")]

    print(f"\n{'=' * 75}")
    print(f"Walk-Forward Validation — {instrument}  ({len(valid)} annual windows)")
    print(f"{'=' * 75}")
    print(
        f"  {'Year':<6}  {'Trades':>6}  {'WR%':>6}  {'PF':>5}  "
        f"{'Net%':>6}  {'MaxDD%':>7}  {'Notes'}"
    )
    print(f"  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*5}  {'-'*6}  {'-'*7}  {'-'*30}")

    for r in rows:
        if r.get("skipped"):
            print(f"  {r['year']:<6}  {'—':>6}  {'—':>6}  {'—':>5}  {'—':>6}  {'—':>7}  {r.get('reason', '')} ({r.get('regime', '')})")
            continue

        pf_str  = f"{r['profit_factor']:.2f}" if r["profit_factor"] != float("inf") else "∞"
        net_str = f"{r['net_pct']:+.1f}%"
        dd_str  = f"{r['max_drawdown_pct']:.1f}%"
        flag    = "✓" if r["profit_factor"] > 1.0 else "✗"
        print(
            f"  {r['year']:<6}  {r['n_trades']:>6}  {r['win_rate']:>5.1f}%  "
            f"{pf_str:>5}  {net_str:>6}  {dd_str:>7}  {flag}  {r['regime']}"
        )

    if not valid:
        print("\n  No valid windows found — check CSV date range.\n")
        return

    # Consistency summary
    profitable    = [r for r in valid if r["profit_factor"] > 1.0]
    good_wr       = [r for r in valid if r["win_rate"] >= 45.0]
    avg_pf        = sum(r["profit_factor"] for r in valid if r["profit_factor"] != float("inf")) / len(valid)
    avg_wr        = sum(r["win_rate"] for r in valid) / len(valid)
    avg_net       = sum(r["net_pct"]  for r in valid) / len(valid)
    worst_dd      = min(r["max_drawdown_pct"] for r in valid)
    total_trades  = sum(r["n_trades"] for r in valid)

    print(f"\n  {'─'*60}")
    print(f"  Profitable years (PF > 1.0):  {len(profitable)}/{len(valid)} = {len(profitable)/len(valid)*100:.0f}%")
    print(f"  Acceptable WR  (≥ 45%):       {len(good_wr)}/{len(valid)} = {len(good_wr)/len(valid)*100:.0f}%")
    print(f"  Average PF across windows:    {avg_pf:.3f}")
    print(f"  Average win rate:             {avg_wr:.1f}%")
    print(f"  Average annual net return:    {avg_net:+.1f}%")
    print(f"  Worst single-year drawdown:   {worst_dd:.1f}%")
    print(f"  Total trades across all years:{total_trades}")

    # Edge consistency verdict
    print(f"\n  {'─'*60}")
    consistent  = len(profitable) / len(valid) >= 0.75
    edge_strong = avg_pf >= 1.3 and avg_wr >= 44.0
    if consistent and edge_strong:
        verdict = "STRONG EDGE — consistent across regimes, deploy with confidence"
    elif consistent:
        verdict = "ACCEPTABLE EDGE — mostly consistent, monitor live closely"
    elif len(profitable) / len(valid) >= 0.60:
        verdict = "MARGINAL EDGE — profitable in most windows, review losing years"
    else:
        verdict = "WEAK EDGE — fails more than 40% of windows, revisit parameters"
    print(f"  Verdict: {verdict}")
    print(f"{'=' * 75}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward validation for LCR strategy")
    parser.add_argument("--instrument", required=True, choices=sorted(_SPREAD_COST.keys()))
    parser.add_argument("--h1-csv",     required=True, help="Path to H1 CSV file")
    parser.add_argument("--balance",    type=float, default=10_000.0)
    args = parser.parse_args()

    df_h1 = _load_csv(args.h1_csv)
    rows  = run_walk_forward(args.instrument, df_h1, initial_balance=args.balance)
    _print_table(args.instrument, rows)


if __name__ == "__main__":
    main()
