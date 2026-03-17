"""Confluence weight optimizer using L1 logistic regression.

Replaces the hand-tuned WEIGHTS dict in engine.py with statistically optimal
coefficients derived from historical trade outcomes (Maximum Likelihood Estimation).

Method:
  1. Run BacktestEngine on all available instruments (in-sample period only).
     OR: query closed live trades from PostgreSQL (--live-trades flag).
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

Run (backtest mode — CSV data):
    docker compose exec engine python -m anchor.backtesting.fit_weights
    docker compose exec engine python -m anchor.backtesting.fit_weights --apply
    docker compose exec engine python -m anchor.backtesting.fit_weights \\
        --instruments EUR_USD,GBP_USD,USD_JPY --end 2024-01-01 --apply

Run (live trade mode — PostgreSQL):
    docker compose exec engine python -m anchor.backtesting.fit_weights --live-trades
    docker compose exec engine python -m anchor.backtesting.fit_weights --live-trades --apply

    Use --live-trades once you have 200+ closed live trades in the DB.
    This is the OOS validation path: live outcomes were not used to design the weights,
    so the regression result is a genuine out-of-sample test.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import warnings

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import StandardScaler

# Suppress sklearn 1.8 FutureWarning about deprecated 'penalty' parameter
warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")

# Silence structlog / engine noise during runs
logging.disable(logging.CRITICAL)

from anchor.backtesting.engine import BacktestEngine

DATA_DIR = Path("/app/data")

# Components stored in the trade log → their WEIGHTS key in engine.py
# csi_score field is reused for oanda_sentiment in the trade log (see engine.py)
COMPONENT_MAP: dict[str, str] = {
    "rsi_score":   "rsi_divergence",
    "bb_kc_score": "bb_kc_squeeze",
    "adx_score":   "adx_filter",
    "sr_score":    "sr_strength",
    "mtf_score":   "mtf_agreement",
    "csi_score":   "oanda_sentiment",
}

# Current weights — must match WEIGHTS dict in engine.py exactly.
# Update this whenever engine.py WEIGHTS change (--apply does it automatically).
CURRENT_WEIGHTS: dict[str, float] = {
    "rsi_divergence":  0.20,
    "bb_kc_squeeze":   0.22,
    "adx_filter":      0.15,
    "sr_strength":     0.20,
    "mtf_agreement":   0.10,
    "oanda_sentiment": 0.04,
    "cot_signal":      0.05,
    "rate_divergence": 0.04,
}

# Preserved weights for macro filters (not in trade log → can't optimize)
_MACRO_RESERVE = {
    "cot_signal":      CURRENT_WEIGHTS["cot_signal"],
    "rate_divergence": CURRENT_WEIGHTS["rate_divergence"],
}

_MIN_LIVE_TRADES = 200   # below this, warn strongly


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


def _collect_live_trades() -> pd.DataFrame:
    """
    Query closed live trades from PostgreSQL, joined to signal component scores.

    JOIN path: trades → signals (via trades.signal_id = signals.id)
    Outcome: net_pl > 0 → win (1), net_pl <= 0 → loss (0)

    Only trades with a linked signal are included — trades without signal_id
    (manual or LCR trades from a different engine path) are excluded because
    they don't have the confluence component scores needed for regression.

    Returns a DataFrame with the same column names as _collect_trades() so
    _fit() works identically regardless of data source.
    """
    from sqlalchemy import create_engine, text
    from anchor.config import get_settings

    settings = get_settings()

    print("  Connecting to PostgreSQL ...", end=" ", flush=True)
    db_engine = create_engine(settings.sync_database_url)

    query = text("""
        SELECT
            s.rsi_score,
            s.bb_kc_score,
            s.adx_score,
            s.sr_score,
            s.mtf_score,
            s.csi_score,
            CASE WHEN t.net_pl > 0 THEN 1 ELSE 0 END AS outcome,
            t.instrument,
            t.session_at_entry,
            t.closed_at,
            t.net_pl
        FROM trades t
        JOIN signals s ON t.signal_id = s.id
        WHERE t.closed_at IS NOT NULL
          AND t.signal_id IS NOT NULL
          AND s.suppressed = FALSE
        ORDER BY t.closed_at
    """)

    with db_engine.connect() as conn:
        df = pd.read_sql(query, conn)

    print(f"{len(df)} closed trades found")
    return df


def _compute_time_weights(df: pd.DataFrame, half_life_days: float = 90.0) -> np.ndarray:
    """Exponential decay weights: recent trades count more.

    weight = exp(-ln(2) / half_life_days * age_days)

    A trade closed today has weight 1.0; one closed half_life_days ago has weight 0.5.
    Requires a 'closed_at' column (datetime). Falls back to uniform weights if absent.
    """
    if "closed_at" not in df.columns:
        return np.ones(len(df))

    now = pd.Timestamp.utcnow().tz_localize(None)
    closed = pd.to_datetime(df["closed_at"], utc=True).dt.tz_localize(None)
    age_days = (now - closed).dt.total_seconds() / 86400.0
    age_days = age_days.clip(lower=0.0).values

    lam = np.log(2.0) / half_life_days
    weights = np.exp(-lam * age_days)
    # Normalize so weights sum to len(df) — keeps effective sample size interpretable
    weights = weights / weights.mean()
    return weights.astype(float)


def _fit(df: pd.DataFrame, C: float = 1.0, sample_weight: np.ndarray | None = None) -> dict[str, float]:
    """
    Fit L1 logistic regression. Returns normalized weights dict.

    The scaler is applied so all features have equal variance before
    regularization — prevents L1 from penalizing small-scale components
    (e.g., mtf_score which has low variance) unfairly.

    sample_weight: per-sample weights (e.g. from _compute_time_weights).
        None = uniform weights (original behaviour).
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
    clf.fit(X_sc, y, sample_weight=sample_weight)

    # Cross-validation accuracy (5-fold)
    cv_scores = cross_val_score(clf, X_sc, y, cv=5, scoring="accuracy",
                                 fit_params={"sample_weight": sample_weight} if sample_weight is not None else {})
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


def _patch_current_weights(new: dict[str, float], self_path: Path) -> None:
    """Update CURRENT_WEIGHTS in this file to match the newly applied weights."""
    src = self_path.read_text()

    lines = ["CURRENT_WEIGHTS: dict[str, float] = {\n"]
    for k, v in new.items():
        lines.append(f'    "{k}": {v},\n')
    lines.append("}\n")
    new_block = "".join(lines).rstrip()

    new_src = re.sub(
        r"CURRENT_WEIGHTS\s*:\s*dict\[str,\s*float\]\s*=\s*\{[^}]+\}",
        new_block,
        src,
        count=1,
    )
    if new_src == src:
        print("  WARNING: could not locate CURRENT_WEIGHTS block — update fit_weights.py manually.")
        return

    self_path.write_text(new_src)
    print(f"  Updated CURRENT_WEIGHTS in {self_path}")


def _log_system_event(new: dict[str, float], source: str, n_trades: int) -> None:
    """Write a WEIGHTS_UPDATED SystemEvent to PostgreSQL for audit trail."""
    try:
        from sqlalchemy import create_engine, text
        from anchor.config import get_settings

        settings = get_settings()
        db_engine = create_engine(settings.sync_database_url)

        now = datetime.now(tz=timezone.utc)
        payload = {
            "old_weights": CURRENT_WEIGHTS,
            "new_weights": new,
            "source": source,          # "backtest" or "live_trades"
            "n_trades": n_trades,
            "applied_at": now.isoformat(),
        }

        stmt = text("""
            INSERT INTO system_events (event_at, event_type, severity, component, message, metadata)
            VALUES (:event_at, 'WEIGHTS_UPDATED', 'INFO', 'fit_weights', :message, :metadata::jsonb)
        """)

        import json
        with db_engine.begin() as conn:
            conn.execute(stmt, {
                "event_at": now,
                "message": f"Confluence weights updated from {source} ({n_trades} trades)",
                "metadata": json.dumps(payload),
            })

        print("  Audit event written to system_events table")

    except Exception as exc:
        print(f"  WARNING: could not write audit event to DB: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Optimize confluence engine weights via L1 logistic regression"
    )
    parser.add_argument(
        "--live-trades",
        action="store_true",
        help=(
            "Use closed live trades from PostgreSQL instead of CSV backtest data. "
            f"Requires ≥ {_MIN_LIVE_TRADES} closed trades in the DB. "
            "This is the OOS validation path — use once you have sufficient live data."
        ),
    )
    parser.add_argument(
        "--instruments",
        default="EUR_USD,GBP_USD,USD_JPY,AUD_USD,GBP_JPY",
        help="Comma-separated pairs for backtest mode (ignored with --live-trades). "
             "Default: 5 majors",
    )
    parser.add_argument(
        "--end",
        default="2024-01-01",
        help="In-sample end date for backtest mode (ignored with --live-trades). "
             "Default: 2024-01-01",
    )
    parser.add_argument(
        "--C",
        type=float,
        default=1.0,
        help="L1 regularization strength. Lower = more sparse (fewer components). Default: 1.0",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Automatically patch WEIGHTS in engine.py and CURRENT_WEIGHTS in this file, "
             "and write an audit event to the system_events table.",
    )
    parser.add_argument(
        "--time-weighted",
        action="store_true",
        help="Apply exponential decay to sample weights so recent trades count more. "
             "Use --half-life to tune the decay rate. Only meaningful with --live-trades.",
    )
    parser.add_argument(
        "--half-life",
        type=float,
        default=90.0,
        help="Half-life in days for time-weighted mode. A trade closed N days ago has "
             "weight exp(-ln(2)/half_life * N). Default: 90 days.",
    )
    args = parser.parse_args()

    instruments = [i.strip() for i in args.instruments.split(",")]
    engine_path  = Path("/app/anchor/signals/engine.py")
    self_path    = Path("/app/anchor/backtesting/fit_weights.py")

    print(f"\n{'='*62}")
    print("CONFLUENCE WEIGHT OPTIMIZER  (L1 Logistic Regression)")
    print(f"{'='*62}")

    if args.live_trades:
        source = "live_trades"
        tw_note = f"  (half-life {args.half_life}d)" if args.time_weighted else ""
        print(f"  Mode        : LIVE TRADES (PostgreSQL OOS validation){tw_note}")
        print(f"  Time-weight : {'YES — recent trades weighted higher' if args.time_weighted else 'NO — uniform weights'}")
        print(f"  Minimum     : {_MIN_LIVE_TRADES} trades required")
        print(f"  L1 strength : C={args.C}")
        print(f"  Engine path : {engine_path}")
        print()

        print("Step 1 — Querying live trades from PostgreSQL...")
        df = _collect_live_trades()

        if df.empty or len(df) < 50:
            print(f"\nERROR: only {len(df)} closed live trades — need ≥ 50 for regression.")
            print("Keep the system running and retry when more trades have closed.")
            sys.exit(1)

        if len(df) < _MIN_LIVE_TRADES:
            print(
                f"\n  WARNING: {len(df)} trades found — below the {_MIN_LIVE_TRADES} minimum "
                f"for statistically reliable L1 regression.\n"
                f"  Results are indicative only. Re-run at {_MIN_LIVE_TRADES}+ trades.\n"
            )

    else:
        source = "backtest"
        print("  Mode        : BACKTEST (CSV in-sample data)")
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

    n_trades = len(df)
    vc = df["outcome"].value_counts()
    print(f"\n  Total trades : {n_trades}")
    print(f"  Wins  : {vc.get(1, 0)} ({vc.get(1, 0)/n_trades*100:.1f}%)")
    print(f"  Losses: {vc.get(0, 0)} ({vc.get(0, 0)/n_trades*100:.1f}%)")

    if args.live_trades and "instrument" in df.columns:
        print(f"\n  Breakdown by instrument:")
        for inst, grp in df.groupby("instrument"):
            wins = int((grp["outcome"] == 1).sum())
            print(f"    {inst:<12}  {len(grp):>4} trades  WR {wins/len(grp)*100:.1f}%")

    print("\nStep 2 — Fitting L1 logistic regression...")
    sample_weight = None
    if getattr(args, "time_weighted", False):
        sample_weight = _compute_time_weights(df, half_life_days=args.half_life)
        eff_n = float(np.sum(sample_weight) ** 2 / np.sum(sample_weight ** 2))
        print(f"  Time-weighting applied (half-life={args.half_life}d, effective N≈{eff_n:.0f})")
    new_weights = _fit(df, C=args.C, sample_weight=sample_weight)

    print("\nStep 3 — Results")
    _compare(new_weights)

    print("\n  New WEIGHTS (copy-paste into engine.py if not using --apply):")
    print("  WEIGHTS = {")
    for k, v in new_weights.items():
        print(f'      "{k}": {v},')
    print("  }")

    if args.apply:
        print("\nStep 4 — Patching files and writing audit trail...")
        if not engine_path.exists():
            print(f"\n  ERROR: {engine_path} not found — cannot auto-patch.")
        else:
            _patch_engine(new_weights, engine_path)
        if self_path.exists():
            _patch_current_weights(new_weights, self_path)
        _log_system_event(new_weights, source, n_trades)
    else:
        print("\n  Run with --apply to automatically update engine.py and log the change.")

    print(f"\n{'='*62}\n")


if __name__ == "__main__":
    main()
