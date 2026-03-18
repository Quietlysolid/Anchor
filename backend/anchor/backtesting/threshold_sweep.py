"""
Threshold Sensitivity Analysis.

Sweeps the confluence threshold across a range of values to measure
edge robustness. Answers: "Does the edge hold at 0.70 or 0.74?"

A robust edge shows monotonically better quality (higher PF, lower DD)
as the threshold rises, without dramatic cliff drops.

Tests both LCR strategy and London Trend strategy.

Usage:
    # LCR (default)
    python -m anchor.backtesting.threshold_sweep \\
        --instrument EUR_USD \\
        --h1-csv data/EUR_USD_H1.csv

    # London Trend
    python -m anchor.backtesting.threshold_sweep \\
        --instrument EUR_USD \\
        --strategy london \\
        --h1-csv data/EUR_USD_H1.csv \\
        --h4-csv data/EUR_USD_H4.csv \\
        --d-csv  data/EUR_USD_D.csv

    make threshold-sweep PAIR=EUR_USD
    make threshold-sweep-london PAIR=EUR_USD
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd


# ── LCR sweep ─────────────────────────────────────────────────────────────────

def _sweep_lcr(
    instrument: str,
    df_h1: "pd.DataFrame",
    thresholds: list[float],
    initial_balance: float,
) -> list[dict[str, Any]]:
    """Run LCR backtest at each threshold by monkey-patching the module constant."""
    import anchor.signals.london_close_reversion as _lcr_mod
    from anchor.backtesting.lcr_backtest import LCRBacktestEngine

    orig = _lcr_mod.LCR_CONFLUENCE_THRESHOLD
    rows: list[dict[str, Any]] = []

    for thresh in thresholds:
        _lcr_mod.LCR_CONFLUENCE_THRESHOLD = thresh
        try:
            engine = LCRBacktestEngine(initial_balance=initial_balance)
            result = engine.run(instrument, df_h1)
            stats  = result.get("stats", {})
            rows.append({
                "threshold":        thresh,
                "n_trades":         stats.get("n_trades", 0),
                "win_rate":         stats.get("win_rate", 0.0),
                "profit_factor":    stats.get("profit_factor", 0.0),
                "net_pct":          stats.get("net_pct", 0.0),
                "max_drawdown_pct": stats.get("max_drawdown_pct", 0.0),
                "trades_per_month": stats.get("trades_per_month", 0.0),
            })
        except Exception as exc:
            rows.append({
                "threshold": thresh, "n_trades": 0, "win_rate": 0.0,
                "profit_factor": 0.0, "net_pct": 0.0, "max_drawdown_pct": 0.0,
                "trades_per_month": 0.0, "error": str(exc)[:80],
            })

    _lcr_mod.LCR_CONFLUENCE_THRESHOLD = orig
    return rows


# ── London Trend sweep ────────────────────────────────────────────────────────

def _sweep_london(
    instrument: str,
    df_h1: "pd.DataFrame",
    df_h4: "pd.DataFrame | None",
    df_d:  "pd.DataFrame | None",
    thresholds: list[float],
    initial_balance: float,
) -> list[dict[str, Any]]:
    """Run London Trend backtest at each threshold by patching settings."""
    import anchor.signals.engine as _eng_mod
    from anchor.backtesting.engine import BacktestEngine

    orig = getattr(_eng_mod.settings, "min_confluence_score", 0.72)
    rows: list[dict[str, Any]] = []

    for thresh in thresholds:
        # Bypass Pydantic frozen model via __dict__ or object.__setattr__
        try:
            object.__setattr__(_eng_mod.settings, "min_confluence_score", thresh)
        except Exception:
            _eng_mod.settings.__dict__["min_confluence_score"] = thresh

        eng = BacktestEngine(initial_balance=initial_balance)
        eng.load_df(instrument, "H1", df_h1)
        if df_h4 is not None:
            eng.load_df(instrument, "H4", df_h4)
        if df_d is not None:
            eng.load_df(instrument, "D", df_d)

        try:
            res = eng.run(instrument=instrument, timeframe="H1")
            rows.append({
                "threshold":        thresh,
                "n_trades":         res.total_trades,
                "win_rate":         res.win_rate * 100,
                "profit_factor":    res.profit_factor,
                "net_pct":          res.net_pnl_pct,
                "max_drawdown_pct": res.max_drawdown_pct,
                "trades_per_month": 0.0,
            })
        except Exception as exc:
            rows.append({
                "threshold": thresh, "n_trades": 0, "win_rate": 0.0,
                "profit_factor": 0.0, "net_pct": 0.0, "max_drawdown_pct": 0.0,
                "trades_per_month": 0.0, "error": str(exc)[:80],
            })

    # Restore original threshold
    try:
        object.__setattr__(_eng_mod.settings, "min_confluence_score", orig)
    except Exception:
        _eng_mod.settings.__dict__["min_confluence_score"] = orig

    return rows


# ── Output ────────────────────────────────────────────────────────────────────

def _print_table(rows: list[dict], instrument: str, strategy: str) -> None:
    H = "\033[1m"
    N = "\033[0m"
    G = "\033[92m"
    R = "\033[91m"

    print(f"\n{'=' * 80}")
    print(f"{H}THRESHOLD SENSITIVITY — {instrument} — {strategy.upper()} strategy{N}")
    print(f"{'=' * 80}")
    print(f"  {'Thresh':>7}  {'Trades':>7}  {'WR%':>6}  {'PF':>6}  "
          f"{'Net%':>7}  {'MaxDD%':>8}  {'T/mo':>5}")
    print(f"  {'-'*7}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*7}  {'-'*8}  {'-'*5}")

    # Find row with best risk-adjusted quality (PF / |MaxDD| proxy)
    best_idx = -1
    best_score = -999.0
    for i, r in enumerate(rows):
        if r.get("n_trades", 0) > 10 and r.get("max_drawdown_pct", 0) != 0:
            score = r["profit_factor"] / max(0.1, abs(r["max_drawdown_pct"]) / 10)
            if score > best_score:
                best_score = score
                best_idx = i

    for i, r in enumerate(rows):
        marker = " ← best risk-adj" if i == best_idx else ""
        if r.get("error"):
            print(f"  {r['threshold']:>7.2f}  ERROR: {r['error']}")
            continue
        pf_col = G if r["profit_factor"] > 1.0 else R
        net_col = G if r["net_pct"] > 0 else R
        print(
            f"  {r['threshold']:>7.2f}  {r['n_trades']:>7}  {r['win_rate']:>5.1f}%  "
            f"{pf_col}{r['profit_factor']:>6.3f}{N}  "
            f"{net_col}{r['net_pct']:>+6.1f}%{N}  "
            f"{r['max_drawdown_pct']:>7.1f}%  {r['trades_per_month']:>4.1f}"
            f"{marker}"
        )

    valid = [r for r in rows if not r.get("error") and r.get("n_trades", 0) > 10]
    if len(valid) >= 2:
        pf_values = [r["profit_factor"] for r in valid]
        pf_range  = max(pf_values) - min(pf_values)
        print(f"\n  {'─'*60}")
        print(f"  PF range across thresholds: {min(pf_values):.3f} → {max(pf_values):.3f}  (spread = {pf_range:.3f})")
        if pf_range < 0.15:
            print(f"  {G}ROBUST{N}: PF spread < 0.15 — edge doesn't depend heavily on exact threshold")
        elif pf_range < 0.30:
            print("  MODERATE: PF spread 0.15-0.30 — threshold matters, stay near optimal")
        else:
            print(f"  {R}FRAGILE{N}: PF spread > 0.30 — edge is threshold-sensitive, verify OOS carefully")

    print(f"\n{H}INTERPRETATION:{N}")
    print("  Robust edge: PF stays > 1.0 across most thresholds")
    print("  Cliff drop:  PF collapses at ±0.02 → threshold is critical, use conservatively")
    print("  Trade-off:   Higher threshold = fewer trades but higher quality per trade")
    print(f"{'=' * 80}\n")


# ── CSV loader ────────────────────────────────────────────────────────────────

def _load_df(path: str) -> "pd.DataFrame":
    import pandas as pd
    df = pd.read_csv(path, parse_dates=["time"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Confluence threshold sensitivity sweep")
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--strategy",   default="lcr", choices=["lcr", "london"],
                        help="Strategy to sweep: lcr (default) or london")
    parser.add_argument("--h1-csv",  required=True, help="Path to H1 CSV")
    parser.add_argument("--h4-csv",  default=None,  help="Path to H4 CSV (London Trend only)")
    parser.add_argument("--d-csv",   default=None,  help="Path to Daily CSV (London Trend only)")
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument(
        "--thresholds", default=None,
        help="Comma-separated threshold list, e.g. '0.45,0.50,0.55,0.60,0.65,0.70'"
    )
    args = parser.parse_args()

    if args.thresholds:
        thresholds = [float(t.strip()) for t in args.thresholds.split(",")]
    elif args.strategy == "lcr":
        thresholds = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
    else:
        thresholds = [0.65, 0.68, 0.70, 0.72, 0.74, 0.76, 0.78]

    if args.strategy == "lcr":
        df_raw = _load_df(args.h1_csv)
        # LCR engine expects time as index
        df_h1 = df_raw.set_index("time") if "time" in df_raw.columns else df_raw
        rows = _sweep_lcr(args.instrument, df_h1, thresholds, args.balance)
    else:
        df_h1 = _load_df(args.h1_csv)
        df_h4 = _load_df(args.h4_csv) if args.h4_csv and Path(args.h4_csv).exists() else None
        df_d  = _load_df(args.d_csv)  if args.d_csv  and Path(args.d_csv).exists()  else None
        rows  = _sweep_london(args.instrument, df_h1, df_h4, df_d, thresholds, args.balance)

    _print_table(rows, args.instrument, args.strategy)


if __name__ == "__main__":
    main()
