"""
LCR Robustness Sweep.

Tests LCR performance stability across parameterized stress scenarios.
Runs each scenario on every active LCR pair and prints a compact comparison table.

Usage (inside container):
    python -m anchor.backtesting.lcr_robustness

Or for a single pair:
    python -m anchor.backtesting.lcr_robustness --instrument EUR_USD
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

from anchor.backtesting.lcr_backtest import LCRBacktestEngine, _load_csv

# ── Instruments and their CSV paths ─────────────────────────────────────────────
_ACTIVE_PAIRS = ["EUR_USD", "NZD_USD", "USD_CAD", "EUR_JPY", "AUD_USD"]

_DATA_DIR = os.environ.get("LCR_DATA_DIR", "/app/data")


def _csv_path(instrument: str) -> str:
    return os.path.join(_DATA_DIR, f"{instrument}_H1.csv")


# ── Scenarios ──────────────────────────────────────────────────────────────────
# Each tuple: (label, spread_mult, slippage_pips, session_hours, partial_tp_rr)
_SCENARIOS: list[tuple[str, float, float, set[int] | None, float | None]] = [
    # label                    spread  slip  hours        ptp_rr
    ("baseline",               1.0,    0.0,  None,        1.5),
    ("spread_2x",              2.0,    0.0,  None,        1.5),
    ("spread_3x",              3.0,    0.0,  None,        1.5),
    ("spread_5x",              5.0,    0.0,  None,        1.5),
    ("slip_0.5pip",            1.0,    0.5,  None,        1.5),
    ("slip_1pip",              1.0,    1.0,  None,        1.5),
    ("slip_2pip",              1.0,    2.0,  None,        1.5),
    ("window_early_16-18",     1.0,    0.0,  {16,17,18},  1.5),
    ("window_late_18-20",      1.0,    0.0,  {18,19,20},  1.5),
    ("ptp_1.0R",               1.0,    0.0,  None,        1.0),
    ("ptp_2.0R",               1.0,    0.0,  None,        2.0),
    ("no_partial_tp",          1.0,    0.0,  None,        None),
    ("spread2x_slip1pip",      2.0,    1.0,  None,        1.5),   # combined friction stress
]


def _run_scenario(
    engine: LCRBacktestEngine,
    instrument: str,
    df: pd.DataFrame,
    scenario: tuple,
) -> dict:
    label, spread_mult, slippage_pips, session_hours, partial_tp_rr = scenario
    result = engine.run(
        instrument=instrument,
        df_h1=df,
        spread_mult=spread_mult,
        slippage_pips=slippage_pips,
        session_hours=session_hours,
        partial_tp_rr=partial_tp_rr,
    )
    stats = result.get("stats", {})
    return {
        "scenario":    label,
        "n":           stats.get("n_trades", 0),
        "wr":          stats.get("win_rate", 0.0),
        "pf":          stats.get("profit_factor", 0.0),
        "max_dd":      stats.get("max_drawdown_pct", 0.0),
        "net_pct":     stats.get("net_pct", 0.0),
        "tpm":         stats.get("trades_per_month", 0.0),
    }


def _print_pair_table(instrument: str, rows: list[dict]) -> None:
    print(f"\n{'='*76}")
    print(f"  {instrument}")
    print(f"{'='*76}")
    hdr = f"  {'Scenario':<22}  {'N':>5}  {'WR%':>6}  {'PF':>6}  {'MaxDD%':>7}  {'Net%':>7}  {'T/mo':>5}"
    print(hdr)
    print(f"  {'-'*22}  {'-'*5}  {'-'*6}  {'-'*6}  {'-'*7}  {'-'*7}  {'-'*5}")
    baseline_pf = next((r["pf"] for r in rows if r["scenario"] == "baseline"), 1.0)
    for r in rows:
        # flag scenarios where PF drops below 1.0 or degrades >25% from baseline
        flag = ""
        if r["pf"] < 1.0:
            flag = " !"
        elif baseline_pf > 1.0 and r["pf"] < baseline_pf * 0.75:
            flag = " ~"
        print(
            f"  {r['scenario']:<22}  {r['n']:>5}  {r['wr']:>6.1f}  "
            f"{r['pf']:>6.3f}  {r['max_dd']:>7.1f}  {r['net_pct']:>7.1f}  {r['tpm']:>5.1f}{flag}"
        )
    print()


def run_sweep(instruments: list[str]) -> None:
    engine = LCRBacktestEngine(initial_balance=10_000.0)

    for instrument in instruments:
        path = _csv_path(instrument)
        if not os.path.exists(path):
            print(f"  [SKIP] {instrument}: CSV not found at {path}")
            continue

        print(f"\nLoading {instrument} H1 data from {path}...")
        try:
            df = _load_csv(path)
        except Exception as exc:
            print(f"  [ERROR] {instrument}: {exc}")
            continue

        print(f"  {len(df)} bars loaded. Running {len(_SCENARIOS)} scenarios...")
        rows = []
        for scenario in _SCENARIOS:
            row = _run_scenario(engine, instrument, df, scenario)
            rows.append(row)
            print(f"    {row['scenario']:<22}  PF={row['pf']:.3f}  WR={row['wr']:.1f}%  DD={row['max_dd']:.1f}%")

        _print_pair_table(instrument, rows)

    print("\nLegend: ! = PF < 1.0 (edge lost)   ~ = PF degraded >25% from baseline\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="LCR Robustness Sweep")
    parser.add_argument("--instrument", default=None, help="Single instrument (default: all active pairs)")
    args = parser.parse_args()

    instruments = [args.instrument] if args.instrument else _ACTIVE_PAIRS
    run_sweep(instruments)


if __name__ == "__main__":
    main()
