"""Instrument confidence analysis using the Information Coefficient (IC).

For each instrument, measures how reliably signal component scores predict
trade outcomes using Spearman rank correlation and rolling stability.

What is IC?
  IC = Spearman(rank(signal_score), rank(outcome)) per component per instrument.
  IC = 0.0 → component has zero predictive value on this pair
  IC > 0.0 → higher score = better chance of winning (useful signal)
  IC < 0.0 → higher score = worse outcome (signal is inverted or noisy)

  An IC > 0.05 is considered 'weak edge'. > 0.10 is 'meaningful edge'.
  Most strategies run with IC 0.03–0.08. > 0.15 is very strong.

Ranking metric — Information Ratio (IR):
  IR = IC_mean / IC_std (across rolling 20-trade windows)
  High IR = consistent signal quality (IC doesn't flip sign across periods)
  Low IR = variable edge (works sometimes, fails other times)

Decision framework:
  Keep pair if:  IC_mean > 0.03  AND  IR > 0.2  AND  trades ≥ 30
  Deprioritize:  IC_mean < 0.03  OR  IR < 0.1
  Remove:        IC_mean < 0  OR  PF < 1.0  AND  trades ≥ 50

Run:
    docker compose exec engine python -m anchor.backtesting.instrument_confidence
    docker compose exec engine python -m anchor.backtesting.instrument_confidence \\
        --instruments EUR_USD,GBP_USD,USD_JPY,AUD_USD,GBP_JPY \\
        --end 2024-01-01
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

logging.disable(logging.CRITICAL)

from anchor.backtesting.engine import BacktestEngine

DATA_DIR = Path("/app/data")

SCORE_COLS = [
    "rsi_score",
    "bb_kc_score",
    "adx_score",
    "sr_score",
    "mtf_score",
    "csi_score",       # reused for oanda_sentiment in trade log
    "confluence_score",
]

# Instruments to evaluate by default
DEFAULT_INSTRUMENTS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD",
    "NZD_USD", "USD_CHF", "EUR_GBP", "GBP_JPY",
]

W = "\033[92m"   # green
R = "\033[91m"   # red
Y = "\033[93m"   # yellow
N = "\033[0m"    # reset
B = "\033[1m"    # bold


def _rolling_ic(scores: np.ndarray, outcomes: np.ndarray, window: int = 20) -> list[float]:
    """Compute rolling Spearman IC over a sliding window."""
    ics = []
    for i in range(window, len(scores) + 1):
        s = scores[i - window: i]
        o = outcomes[i - window: i]
        if s.std() < 1e-8:
            continue
        ic, _ = scipy_stats.spearmanr(s, o)
        if not np.isnan(ic):
            ics.append(float(ic))
    return ics


def _analyze_instrument(
    inst: str,
    end_date: str,
    ic_window: int = 20,
) -> dict | None:
    h1 = DATA_DIR / f"{inst}_H1.csv"
    h4 = DATA_DIR / f"{inst}_H4.csv"
    d1 = DATA_DIR / f"{inst}_D.csv"

    if not h1.exists():
        return None

    print(f"  {inst} ...", end=" ", flush=True)
    eng = BacktestEngine(initial_balance=10_000.0)
    eng.load_csv(inst, "H1", str(h1))
    if h4.exists():
        eng.load_csv(inst, "H4", str(h4))
    if d1.exists():
        eng.load_csv(inst, "D", str(d1))

    try:
        results = eng.run(inst, "H1")
    except Exception as exc:
        print(f"ERROR ({exc})")
        return None

    trades = [t for t in results.trade_log if t["entry_time"] < end_date]
    print(f"{len(trades)} in-sample trades")

    if len(trades) < 20:
        return {"instrument": inst, "trades": len(trades), "error": "too_few_trades"}

    df = pd.DataFrame(trades)
    outcomes = df["outcome"].values.astype(float)

    pnls = df["net_pl"].values
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    pf = float(wins.sum() / abs(losses.sum())) if len(losses) > 0 else float("inf")
    wr = float((outcomes == 1).mean())

    # Per-component IC
    component_ics: dict[str, dict] = {}
    for col in SCORE_COLS:
        if col not in df.columns:
            continue
        scores = df[col].fillna(0.0).values.astype(float)
        if scores.std() < 1e-6:
            component_ics[col] = {"ic_mean": 0.0, "ic_std": 0.0, "ir": 0.0}
            continue
        rolling_ics = _rolling_ic(scores, outcomes, window=ic_window)
        if not rolling_ics:
            component_ics[col] = {"ic_mean": 0.0, "ic_std": 0.0, "ir": 0.0}
            continue
        ic_mean = float(np.mean(rolling_ics))
        ic_std  = float(np.std(rolling_ics)) if len(rolling_ics) > 1 else 0.0
        ir = ic_mean / ic_std if ic_std > 1e-6 else 0.0
        component_ics[col] = {"ic_mean": round(ic_mean, 4), "ic_std": round(ic_std, 4), "ir": round(ir, 3)}

    # Overall instrument IC (use confluence_score as summary)
    conf_ics = _rolling_ic(
        df["confluence_score"].fillna(0.0).values.astype(float), outcomes, ic_window
    )
    overall_ic   = float(np.mean(conf_ics)) if conf_ics else 0.0
    overall_ir   = (overall_ic / np.std(conf_ics)) if (conf_ics and np.std(conf_ics) > 1e-6) else 0.0

    # Verdict
    if overall_ic >= 0.05 and overall_ir >= 0.2 and pf >= 1.05 and len(trades) >= 30:
        verdict = "KEEP"
    elif overall_ic < 0.0 or (pf < 1.0 and len(trades) >= 50):
        verdict = "REMOVE"
    else:
        verdict = "REVIEW"

    return {
        "instrument": inst,
        "trades":     len(trades),
        "win_rate":   round(wr, 4),
        "profit_factor": round(pf, 3),
        "overall_ic":  round(overall_ic, 4),
        "overall_ir":  round(overall_ir, 3),
        "components": component_ics,
        "verdict":    verdict,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure per-instrument signal reliability via IC analysis"
    )
    parser.add_argument(
        "--instruments",
        default=",".join(DEFAULT_INSTRUMENTS),
        help="Comma-separated pairs to analyze",
    )
    parser.add_argument(
        "--end",
        default="2024-01-01",
        help="In-sample end date (exclusive). (default: 2024-01-01)",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=20,
        help="Rolling IC window size in trades. (default: 20)",
    )
    args = parser.parse_args()

    instruments = [i.strip() for i in args.instruments.split(",")]

    print(f"\n{'='*70}")
    print("INSTRUMENT CONFIDENCE ANALYSIS  (Information Coefficient)")
    print(f"{'='*70}")
    print(f"  In-sample period : up to {args.end}")
    print(f"  IC window        : {args.window} trades")
    print()

    print("Running backtests and computing IC...")
    results = []
    for inst in instruments:
        r = _analyze_instrument(inst, end_date=args.end, ic_window=args.window)
        if r:
            results.append(r)

    if not results:
        print("No results — check data files in /app/data/")
        return

    # Sort by overall IR (best signal consistency first)
    results.sort(key=lambda x: x.get("overall_ir", -999), reverse=True)

    # ── Summary table ─────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"{B}INSTRUMENT RANKING BY SIGNAL RELIABILITY{N}")
    print(f"{'='*70}")
    print(f"  {'Pair':<10}  {'Trades':>6}  {'WR':>6}  {'PF':>5}  {'IC':>7}  {'IR':>7}  {'Verdict'}")
    print(f"  {'-'*10}  {'-'*6}  {'-'*6}  {'-'*5}  {'-'*7}  {'-'*7}  {'-'*8}")

    for r in results:
        if "error" in r:
            print(f"  {r['instrument']:<10}  {r['trades']:>6}  {'n/a':>6}  {'n/a':>5}  {'n/a':>7}  {'n/a':>7}  {Y}SKIP (too few trades){N}")
            continue
        verdict = r["verdict"]
        v_color = W if verdict == "KEEP" else (R if verdict == "REMOVE" else Y)
        ic_color = W if r["overall_ic"] >= 0.05 else (R if r["overall_ic"] < 0 else Y)
        ir_color = W if r["overall_ir"] >= 0.2 else (R if r["overall_ir"] < 0.1 else Y)
        print(
            f"  {r['instrument']:<10}  {r['trades']:>6}  {r['win_rate']*100:>5.1f}%  {r['profit_factor']:>5.2f}"
            f"  {ic_color}{r['overall_ic']:>+7.4f}{N}  {ir_color}{r['overall_ir']:>+7.3f}{N}"
            f"  {v_color}{verdict}{N}"
        )

    # ── Component IC breakdown ────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"{B}COMPONENT IC BY INSTRUMENT  (which signals work on which pairs?){N}")
    print(f"{'='*70}")

    headers = [f"{'Component':<20}"] + [f"{r['instrument']:>10}" for r in results if "components" in r]
    print("  " + "  ".join(headers))
    print("  " + "  ".join(["-"*20] + ["-"*10] * len([r for r in results if "components" in r])))

    for col in SCORE_COLS:
        row = f"  {col:<20}"
        for r in results:
            if "components" not in r:
                continue
            comp = r["components"].get(col, {})
            ic = comp.get("ic_mean", 0.0)
            color = W if ic >= 0.05 else (R if ic < 0 else Y)
            row += f"  {color}{ic:>+10.4f}{N}"
        print(row)

    # ── Recommendations ───────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"{B}RECOMMENDATIONS{N}")
    print(f"{'='*70}")

    keep = [r for r in results if r.get("verdict") == "KEEP"]
    remove = [r for r in results if r.get("verdict") == "REMOVE"]
    review = [r for r in results if r.get("verdict") == "REVIEW"]

    if keep:
        pairs = [r["instrument"] for r in keep]
        print(f"\n  {W}KEEP{N}   ({len(keep)} pairs): {', '.join(pairs)}")
        print(f"  → These show consistent IC > 0.05 and IR > 0.2")

    if remove:
        pairs = [r["instrument"] for r in remove]
        print(f"\n  {R}REMOVE{N} ({len(remove)} pairs): {', '.join(pairs)}")
        print(f"  → IC < 0 or PF < 1.0 with sufficient sample — the signal has no edge here")

    if review:
        pairs = [r["instrument"] for r in review]
        print(f"\n  {Y}REVIEW{N} ({len(review)} pairs): {', '.join(pairs)}")
        print(f"  → Too few trades or marginal IC — collect 3+ months live data before deciding")

    # Target pair config
    optimal = [r["instrument"] for r in results if r.get("verdict") == "KEEP"]
    if optimal:
        print(f"\n  Optimal instruments config for config.py:")
        print(f'  instruments: list[str] = {optimal!r}')

    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    main()
