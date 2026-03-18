"""
Multi-Pair Portfolio Backtest.

Runs LCR backtest on all active pairs simultaneously and aggregates
results into a combined portfolio equity curve. Reports:

  - Per-pair stats: WR, PF, MaxDD, Net%, Trades/mo
  - Portfolio equity curve (all pairs compounded together)
  - Inter-pair monthly return correlation matrix
  - Portfolio-level MaxDD (is it worse than any single pair alone?)
  - Diversification ratio: portfolio_DD / avg_individual_DD

The key question: does running 6 pairs simultaneously cause drawdowns
to compound or diversify? Correlated pairs (EUR_USD + GBP_USD) draw
down together; uncorrelated pairs (EUR_JPY + NZD_USD) offset each other.

Usage:
    python -m anchor.backtesting.portfolio_backtest \\
        --pairs EUR_USD,GBP_USD,NZD_USD,USD_CAD,EUR_JPY,AUD_USD \\
        --data-dir data \\
        --balance 10000

    make portfolio-backtest
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# ── Core runner ───────────────────────────────────────────────────────────────

def run_portfolio_backtest(
    pairs: list[str],
    data_dir: str,
    initial_balance: float = 10_000.0,
) -> dict[str, Any]:
    """Run LCR backtest on all pairs and combine into a portfolio."""
    from anchor.backtesting.lcr_backtest import LCRBacktestEngine, _load_csv

    per_pair: dict[str, dict] = {}

    for pair in pairs:
        csv_path = Path(data_dir) / f"{pair}_H1.csv"
        if not csv_path.exists():
            print(f"  WARNING: {csv_path} not found, skipping {pair}")
            continue

        print(f"  Running LCR backtest: {pair} ...")
        try:
            df_h1  = _load_csv(str(csv_path))
            engine = LCRBacktestEngine(initial_balance=initial_balance)
            result = engine.run(pair, df_h1)
        except Exception as exc:
            print(f"  ERROR: {pair} → {exc}")
            continue

        if result.get("error") or not result.get("trades"):
            print(f"  WARNING: {pair} returned no trades ({result.get('error', 'empty')})")
            continue

        per_pair[pair] = result
        stats = result.get("stats", {})
        print(
            f"    {pair}: {stats.get('n_trades',0)} trades, "
            f"WR {stats.get('win_rate',0):.1f}%, "
            f"PF {stats.get('profit_factor',0):.3f}"
        )

    if not per_pair:
        return {"error": "No pairs had valid results"}

    return _aggregate_portfolio(per_pair, initial_balance)


# ── Aggregation ───────────────────────────────────────────────────────────────

def _aggregate_portfolio(
    per_pair: dict[str, dict],
    initial_balance: float,
) -> dict[str, Any]:
    # Collect all trades, sorted by entry time
    all_trades: list[dict] = []
    for pair, result in per_pair.items():
        for t in result["trades"]:
            trade = dict(t)
            trade["_pair"] = pair
            all_trades.append(trade)

    all_trades.sort(key=lambda t: t.get("entry_time", ""))

    # Portfolio equity curve: compound all pl_pct across all pairs sequentially
    # (conservative: assumes trades don't perfectly overlap — no simultaneous fill model)
    balance = initial_balance
    equity_curve = [balance]
    pair_balance: dict[str, float] = {p: initial_balance for p in per_pair}

    # Monthly P&L per pair for correlation
    pair_monthly: dict[str, dict[str, float]] = {p: {} for p in per_pair}

    for t in all_trades:
        pair    = t["_pair"]
        pl_pct  = t.get("pl_pct", 0.0)
        balance *= (1.0 + pl_pct)
        pair_balance[pair] *= (1.0 + pl_pct)
        equity_curve.append(balance)

        entry = t.get("entry_time")
        if entry is not None:
            if hasattr(entry, "strftime"):
                mk = entry.strftime("%Y-%m")
            elif isinstance(entry, str) and len(entry) >= 7:
                mk = entry[:7]
            else:
                mk = None
            if mk:
                pair_monthly[pair][mk] = pair_monthly[pair].get(mk, 0.0) + pl_pct

    # Portfolio-level stats
    equity_arr = np.array(equity_curve)
    peak       = np.maximum.accumulate(equity_arr)
    dd_arr     = (equity_arr - peak) / peak
    port_max_dd = float(np.min(dd_arr)) * 100

    wins   = [t for t in all_trades if t.get("pl_pct", 0) > 0]
    losses = [t for t in all_trades if t.get("pl_pct", 0) <= 0]
    gross_wins   = sum(t["pl_pct"] for t in wins)
    gross_losses = abs(sum(t["pl_pct"] for t in losses))
    port_pf  = gross_wins / gross_losses if gross_losses > 0 else float("inf")
    port_wr  = len(wins) / len(all_trades) * 100 if all_trades else 0.0
    port_net = (balance / initial_balance - 1.0) * 100

    # Monthly return Sharpe proxy
    all_months = sorted({mk for p in pair_monthly.values() for mk in p})
    port_monthly = [
        sum(pair_monthly[p].get(mk, 0.0) for p in per_pair) / len(per_pair)
        for mk in all_months
    ]
    monthly_arr = np.array(port_monthly)
    port_sharpe = (
        float(np.mean(monthly_arr) / np.std(monthly_arr) * np.sqrt(12))
        if np.std(monthly_arr) > 0 else 0.0
    )

    # Correlation matrix (monthly returns per pair)
    if len(all_months) > 2:
        monthly_df   = pd.DataFrame(
            {p: [pair_monthly[p].get(mk, 0.0) for mk in all_months] for p in per_pair},
            index=all_months,
        )
        corr_matrix = monthly_df.corr()
    else:
        corr_matrix = None

    # Diversification ratio: portfolio_DD / avg_individual_DD
    individual_dds = [
        abs(per_pair[p]["stats"].get("max_drawdown_pct", 0.0))
        for p in per_pair
        if per_pair[p]["stats"].get("max_drawdown_pct") is not None
    ]
    avg_ind_dd = float(np.mean(individual_dds)) if individual_dds else 0.0
    div_ratio  = avg_ind_dd / abs(port_max_dd) if port_max_dd != 0 else 1.0

    return {
        "per_pair":               {p: per_pair[p]["stats"] for p in per_pair},
        "portfolio_total_trades": len(all_trades),
        "portfolio_win_rate":     round(port_wr, 1),
        "portfolio_pf":           round(port_pf, 3),
        "portfolio_net_pct":      round(port_net, 1),
        "portfolio_max_dd":       round(port_max_dd, 1),
        "portfolio_sharpe":       round(port_sharpe, 3),
        "diversification_ratio":  round(div_ratio, 2),
        "avg_individual_max_dd":  round(avg_ind_dd, 1),
        "correlation_matrix":     corr_matrix,
        "equity_curve":           equity_curve,
        "n_pairs":                len(per_pair),
    }


# ── Output ────────────────────────────────────────────────────────────────────

def _print_results(result: dict[str, Any]) -> None:
    H = "\033[1m"
    N = "\033[0m"
    G = "\033[92m"
    R = "\033[91m"
    Y = "\033[93m"

    if result.get("error"):
        print(f"ERROR: {result['error']}")
        return

    n_pairs = result["n_pairs"]
    print(f"\n{'=' * 80}")
    print(f"{H}PORTFOLIO BACKTEST — LCR Strategy — {n_pairs} Pairs{N}")
    print(f"{'=' * 80}")

    # Per-pair table
    print(f"\n{H}PER-PAIR RESULTS:{N}")
    print(f"  {'Pair':<10} {'Trades':>7} {'WR%':>6} {'PF':>6} {'Net%':>7} {'MaxDD%':>8} {'T/mo':>5}")
    print(f"  {'-'*10} {'-'*7} {'-'*6} {'-'*6} {'-'*7} {'-'*8} {'-'*5}")

    for pair, stats in result["per_pair"].items():
        wr  = stats.get("win_rate", 0.0)
        pf  = stats.get("profit_factor", 0.0)
        net = stats.get("net_pct", 0.0)
        dd  = stats.get("max_drawdown_pct", 0.0)
        n   = stats.get("n_trades", 0)
        tpm = stats.get("trades_per_month", 0.0)
        pf_col  = G if pf > 1.0 else R
        net_col = G if net > 0 else R
        flag    = "✓" if pf > 1.0 else "✗"
        print(
            f"  {pair:<10} {n:>7}  {wr:>5.1f}%  "
            f"{pf_col}{pf:>5.3f}{N}  "
            f"{net_col}{net:>+6.1f}%{N}  {dd:>7.1f}%  {tpm:>4.1f}  {flag}"
        )

    # Portfolio summary
    pf   = result["portfolio_pf"]
    dd   = result["portfolio_max_dd"]
    dr   = result["diversification_ratio"]
    wr   = result["portfolio_win_rate"]
    net  = result["portfolio_net_pct"]
    sh   = result["portfolio_sharpe"]
    avg_dd = result["avg_individual_max_dd"]

    pf_col  = G if pf > 1.0 else R
    dd_col  = G if abs(dd) < 20 else (Y if abs(dd) < 30 else R)
    dr_col  = G if dr > 1.0 else (Y if dr > 0.8 else R)

    print(f"\n{H}PORTFOLIO SUMMARY:{N}")
    print(f"  Total trades:             {result['portfolio_total_trades']}")
    print(f"  Portfolio WR:             {wr:.1f}%")
    print(f"  Portfolio PF:             {pf_col}{pf:.3f}{N}")
    print(f"  Portfolio Net%:           {net:+.1f}%")
    print(f"  Portfolio Max DD:         {dd_col}{dd:.1f}%{N}")
    print(f"  Avg individual Max DD:    {avg_dd:.1f}%")
    print(f"  Diversification ratio:    {dr_col}{dr:.2f}x{N}")
    print(f"  Portfolio monthly Sharpe: {sh:.3f}")

    print("\n  Diversification ratio = avg_individual_DD / portfolio_DD")
    if dr >= 1.0:
        print(f"  {G}GOOD{N}: Portfolio DD ({abs(dd):.1f}%) LESS than avg individual DD ({avg_dd:.1f}%) — pairs diversify each other")
    else:
        print(f"  {R}WARN{N}: Portfolio DD ({abs(dd):.1f}%) MORE than avg individual DD ({avg_dd:.1f}%) — pairs amplify drawdowns")

    # Correlation matrix
    corr = result.get("correlation_matrix")
    if corr is not None:
        pairs_list = list(corr.columns)
        print(f"\n{H}MONTHLY RETURN CORRELATIONS:{N}")
        header = f"  {'':12}" + "".join(f"  {p[:8]:>8}" for p in pairs_list)
        print(header)
        for row_pair in pairs_list:
            row = f"  {row_pair:<12}"
            for col_pair in pairs_list:
                v = corr.loc[row_pair, col_pair]
                if row_pair == col_pair:
                    row += f"  {'1.000':>8}"
                elif abs(v) > 0.70:
                    row += f"  {R}{v:>8.3f}{N}"
                elif abs(v) < 0.30:
                    row += f"  {G}{v:>8.3f}{N}"
                else:
                    row += f"  {v:>8.3f}"
            print(row)
        print(f"  ({R}red{N} = corr > 0.70 — pairs move together, {G}green{N} = corr < 0.30 — genuine diversification)")

        # Warn on highly correlated pairs
        high_corr = []
        for i, p1 in enumerate(pairs_list):
            for p2 in pairs_list[i+1:]:
                v = corr.loc[p1, p2]
                if abs(v) > 0.70:
                    high_corr.append((p1, p2, v))
        if high_corr:
            print(f"\n  {Y}WARNING: Highly correlated pairs (> 0.70):{N}")
            for p1, p2, v in high_corr:
                print(f"    {p1} / {p2}: {v:.3f} — consider halving risk on one")

    # Verdict
    print(f"\n{H}VERDICT:{N}")
    issues = []
    if abs(dd) > 30:
        issues.append(f"Portfolio MaxDD {dd:.1f}% exceeds 30% — reduce per-pair risk or remove weakest pair")
    elif abs(dd) > 20:
        issues.append(f"Portfolio MaxDD {dd:.1f}% — acceptable but monitor closely in live trading")
    if dr < 0.80:
        issues.append(f"Low diversification ratio {dr:.2f} — pairs too correlated, combined DD amplified")
    if pf < 1.2:
        issues.append(f"Portfolio PF {pf:.3f} is marginal — individual pair results may not hold combined")

    if not issues:
        print(f"  {G}PASS{N}: Portfolio metrics within acceptable ranges — proceed with 6-pair live deployment")
    else:
        for issue in issues:
            print(f"  {R}ISSUE{N}: {issue}")

    print(f"{'=' * 80}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-pair LCR portfolio backtest")
    parser.add_argument(
        "--pairs",
        default="EUR_USD,GBP_USD,NZD_USD,USD_CAD,EUR_JPY,AUD_USD",
        help="Comma-separated instrument list (default: 6 active LCR pairs)",
    )
    parser.add_argument("--data-dir", default="data",    help="Directory containing H1 CSV files")
    parser.add_argument("--balance",  type=float, default=10_000.0)
    args = parser.parse_args()

    pairs  = [p.strip() for p in args.pairs.split(",")]
    result = run_portfolio_backtest(pairs, args.data_dir, args.balance)
    _print_results(result)


if __name__ == "__main__":
    main()
