"""Confluence weight optimizer using L1 logistic regression.

Replaces the hand-tuned WEIGHTS dict in engine.py with statistically optimal
coefficients derived from historical trade outcomes (Maximum Likelihood Estimation).

Method:
  1. Run BacktestEngine on all available instruments (in-sample period only).
  2. Collect per-trade component scores + binary outcome (TP hit = 1, SL hit = 0).
  3. Fit LogisticRegression(penalty='l1') — L1 regularization drives irrelevant
     component weights toward zero, exposing which factors genuinely predict wins.
  4. Extract non-negative coefficients, normalize to sum = 1.0.
  5. Print the result, compare to current weights. Optionally patch engine.py.

Why L1 logistic regression:
  - Unlike manual tuning, MLE minimizes prediction error on actual trade outcomes.
  - L1 penalty (Lasso) provides automatic feature selection: components that don't
    predict wins are driven to zero weight rather than kept at an arbitrary 0.04.
  - The calibrated probabilities can replace the raw confluence score as the
    trade entry condition (P(win) > breakeven WR = 42.9% for 2:1.5 R:R).

COT and rate_divergence are excluded from regression (not in trade log because
they fail-open at 0.5, adding no variance). Their weights are preserved as-is.

Run:
    docker compose exec engine python -m anchor.backtesting.fit_weights
    docker compose exec engine python -m anchor.backtesting.fit_weights --apply
    docker compose exec engine python -m anchor.backtesting.fit_weights \\
        --instruments EUR_USD,GBP_USD,USD_JPY --end 2024-01-01 --apply
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import StandardScaler

# Silence structlog / engine noise during runs
logging.disable(logging.CRITICAL)

from anchor.backtesting.engine import BacktestEngine

DATA_DIR = Path("/app/data")

# Components stored in the trade log → their WEIGHTS key in engine.py
# csi_score field is reused for oanda_sentiment in the trade log (see engine.py L354)
COMPONENT_MAP: dict[str, str] = {
    "rsi_score":   "rsi_divergence",
    "bb_kc_score": "bb_kc_squeeze",
    "adx_score":   "adx_filter",
    "sr_score":    "sr_strength",
    "mtf_score":   "mtf_agreement",
    "csi_score":   "oanda_sentiment",
}

# Current weights — compared against optimized output
CURRENT_WEIGHTS: dict[str, float] = {
    "rsi_divergence":  0.24,
    "bb_kc_squeeze":   0.20,
    "adx_filter":      0.15,
    "sr_strength":     0.24,
    "mtf_agreement":   0.04,
    "oanda_sentiment": 0.04,
    "cot_signal":      0.05,
    "rate_divergence": 0.04,
}

# Preserved weights for macro filters (not in trade log → can't optimize)
_MACRO_RESERVE = {
    "cot_signal":      CURRENT_WEIGHTS["cot_signal"],
    "rate_divergence": CURRENT_WEIGHTS["rate_divergence"],
}


def _collect_trades(instruments: list[str], end_date: str) -> pd.DataFrame:
    """Run BacktestEngine on each instrument and collect in-sample trade rows."""
    all_trades: list[dict] = []

    for inst in instruments:
        h1 = DATA_DIR / f"{inst}_H1.csv"
        h4 = DATA_DIR / f"{inst}_H4.csv"
        d1 = DATA_DIR / f"{inst}_D.csv"

        if not h1.exists():
            print(f"  [SKIP] {inst}: {h1} not found")
            continue

        print(f"  Backtesting {inst} ...", end=" ", flush=True)
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
            continue

        in_sample = [t for t in results.trade_log if t["entry_time"] < end_date]
        print(f"{results.total_trades} total → {len(in_sample)} in-sample")
        all_trades.extend(in_sample)

    return pd.DataFrame(all_trades) if all_trades else pd.DataFrame()


def _fit(df: pd.DataFrame, C: float = 1.0) -> dict[str, float]:
    """
    Fit L1 logistic regression. Returns normalized weights dict.

    The scaler is applied so all features have equal variance before
    regularization — prevents L1 from penalizing small-scale components
    (e.g., mtf_score which has low variance) unfairly.
    """
    feature_cols = list(COMPONENT_MAP.keys())
    X = df[feature_cols].fillna(0.0).values
    y = df["outcome"].values

    scaler = StandardScaler()
    X_sc = scaler.fit_transform(X)

    clf = LogisticRegression(
        penalty="l1",
        C=C,
        solver="liblinear",
        max_iter=2000,
        random_state=42,
        class_weight="balanced",   # handle slight class imbalance
    )
    clf.fit(X_sc, y)

    # Cross-validation accuracy (5-fold)
    cv_scores = cross_val_score(clf, X_sc, y, cv=5, scoring="accuracy")
    print(f"\n  Cross-validation accuracy: {cv_scores.mean():.3f} ± {cv_scores.std():.3f}")
    print(f"  Breakeven accuracy for this R:R: {1/(1 + 2.0/1.5):.3f}  (need to beat this)")

    coefs = clf.coef_[0]
    print("\n  Raw L1 coefficients (feature → outcome predictive power):")
    for feat, coef in zip(feature_cols, coefs):
        direction = "↑ win" if coef > 0 else "↓ win"
        print(f"    {feat:<20}  {coef:+.4f}  {direction}")

    # Convert to weights:
    # Clamp negatives to a floor of 0.01 — never completely remove a component
    # (ablation testing is the right tool for elimination, not weight optimization).
    weights_raw = {
        COMPONENT_MAP[feat]: max(0.01, float(coef))
        for feat, coef in zip(feature_cols, coefs)
    }

    # Reserve budget for macro filters (COT + rate_divergence)
    macro_total = sum(_MACRO_RESERVE.values())  # 0.09
    opt_total = sum(weights_raw.values())
    scale = (1.0 - macro_total) / opt_total

    final: dict[str, float] = {k: round(v * scale, 4) for k, v in weights_raw.items()}
    final.update(_MACRO_RESERVE)

    # Renormalize to exactly 1.0 (absorb rounding error into largest component)
    total = sum(final.values())
    largest = max(final, key=final.get)
    final[largest] = round(final[largest] + (1.0 - total), 4)

    return final


def _compare(new: dict[str, float]) -> None:
    """Print side-by-side weight comparison."""
    W = "\033[92m"
    R = "\033[91m"
    N = "\033[0m"

    print("\n  Current vs. optimized weights:")
    print(f"  {'Component':<22}  {'Current':>8}  {'Optimized':>9}  {'Delta':>8}")
    print(f"  {'-'*22}  {'-'*8}  {'-'*9}  {'-'*8}")

    for k in CURRENT_WEIGHTS:
        old = CURRENT_WEIGHTS[k]
        new_v = new.get(k, 0.0)
        delta = new_v - old
        color = W if delta >= 0 else R
        sign = "+" if delta >= 0 else ""
        print(f"  {k:<22}  {old:.4f}    {new_v:.4f}    {color}{sign}{delta:.4f}{N}")

    print(f"  {'TOTAL':<22}  {sum(CURRENT_WEIGHTS.values()):.4f}    {sum(new.values()):.4f}")


def _patch_engine(new: dict[str, float], engine_path: Path) -> None:
    """Patch the WEIGHTS dict in engine.py with the new values."""
    src = engine_path.read_text()

    lines = ["WEIGHTS = {\n"]
    for k, v in new.items():
        comment = "  # macro filter, preserved" if k in _MACRO_RESERVE else ""
        lines.append(f'    "{k}": {v},{comment}\n')
    lines.append("}\n")
    new_block = "".join(lines).rstrip()

    new_src = re.sub(r"WEIGHTS\s*=\s*\{[^}]+\}", new_block, src, count=1)
    if new_src == src:
        print("  WARNING: could not locate WEIGHTS block — update engine.py manually.")
        return

    engine_path.write_text(new_src)
    print(f"  Patched WEIGHTS in {engine_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Optimize confluence engine weights via L1 logistic regression"
    )
    parser.add_argument(
        "--instruments",
        default="EUR_USD,GBP_USD,USD_JPY,AUD_USD,GBP_JPY",
        help="Comma-separated pairs to use for training (default: 5 majors)",
    )
    parser.add_argument(
        "--end",
        default="2024-01-01",
        help="In-sample end date (exclusive). OOS = everything after this. (default: 2024-01-01)",
    )
    parser.add_argument(
        "--C",
        type=float,
        default=1.0,
        help="L1 regularization strength. Lower = more sparse (fewer components). (default: 1.0)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Automatically patch the WEIGHTS dict in engine.py",
    )
    args = parser.parse_args()

    instruments = [i.strip() for i in args.instruments.split(",")]
    engine_path = Path("/app/anchor/signals/engine.py")

    print(f"\n{'='*62}")
    print("CONFLUENCE WEIGHT OPTIMIZER  (L1 Logistic Regression)")
    print(f"{'='*62}")
    print(f"  Instruments : {instruments}")
    print(f"  In-sample   : up to {args.end}")
    print(f"  L1 strength : C={args.C}  (lower C → more sparsity)")
    print(f"  Engine path : {engine_path}")
    print()

    print("Step 1 — Collecting trade data via BacktestEngine...")
    df = _collect_trades(instruments, args.end)

    if df.empty or len(df) < 50:
        print(f"\nERROR: only {len(df)} trades — need ≥ 50 for reliable regression.")
        print("Add more instruments or extend the date range.")
        sys.exit(1)

    vc = df["outcome"].value_counts()
    print(f"\n  Total in-sample trades : {len(df)}")
    print(f"  Wins  : {vc.get(1, 0)} ({vc.get(1, 0)/len(df)*100:.1f}%)")
    print(f"  Losses: {vc.get(0, 0)} ({vc.get(0, 0)/len(df)*100:.1f}%)")

    print("\nStep 2 — Fitting L1 logistic regression...")
    new_weights = _fit(df, C=args.C)

    print("\nStep 3 — Results")
    _compare(new_weights)

    print("\n  New WEIGHTS (copy-paste into engine.py if not using --apply):")
    print("  WEIGHTS = {")
    for k, v in new_weights.items():
        print(f'      "{k}": {v},')
    print("  }")

    if args.apply:
        if not engine_path.exists():
            print(f"\n  ERROR: {engine_path} not found — cannot auto-patch.")
        else:
            print("\nStep 4 — Patching engine.py...")
            _patch_engine(new_weights, engine_path)
    else:
        print("\n  Run with --apply to automatically update engine.py.")

    print(f"\n{'='*62}\n")


if __name__ == "__main__":
    main()
