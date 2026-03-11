"""Ablation study runner.

Runs 6 pre-defined ablation tests against a historical CSV dataset and prints
a side-by-side comparison table so you can see exactly which components
contribute positive expectancy.

Tests:
  1. BASELINE       — full system, all components enabled
  2. NO_SENTIMENT   — remove OANDA retail sentiment signal
  3. NO_HMM         — remove HMM VOLATILE regime gate
  4. NO_ML          — remove ML confidence gate
  5. NO_SESSION     — remove session filter (trade all hours)
  6. SENTIMENT_ONLY — only sentiment drives confluence; all other scores neutral

For each test, Sharpe, MaxDD, Win%, Profit Factor, and trade count are reported.
The delta vs BASELINE is shown in green (improvement) or red (degradation).

Usage:
    python -m anchor.backtesting.ablation_runner \\
        --instrument EUR_USD \\
        --h1-csv  data/EURUSD_H1_2018_2024.csv \\
        --h4-csv  data/EURUSD_H4_2018_2024.csv \\
        --d-csv   data/EURUSD_D_2018_2024.csv \\
        --balance 10000 \\
        --export-csv ablation_results.csv

    # Run across all 5 pairs:
    for pair in EUR_USD GBP_USD USD_JPY AUD_USD USD_CAD; do
        python -m anchor.backtesting.ablation_runner \\
            --instrument $pair \\
            --h1-csv data/${pair}_H1.csv \\
            --balance 10000
    done
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field

import pandas as pd

from anchor.backtesting.engine import BacktestEngine
from anchor.backtesting.results import BacktestResults
from anchor.signals.engine import ConfluenceEngine


# ── Ablation test definitions ─────────────────────────────────────────────────

@dataclass
class AblationTest:
    name: str
    description: str
    flags: dict = field(default_factory=dict)   # kwargs forwarded to ConfluenceEngine


ABLATION_TESTS: list[AblationTest] = [
    AblationTest(
        name="BASELINE",
        description="Full system — all components enabled",
        flags={},
    ),
    AblationTest(
        name="NO_SENTIMENT",
        description="Remove OANDA retail sentiment (weight redistributed)",
        flags={"ablation_sentiment": False},
    ),
    AblationTest(
        name="NO_HMM",
        description="Remove HMM VOLATILE gate (all regimes tradeable)",
        flags={"ablation_hmm_gate": False},
    ),
    AblationTest(
        name="NO_ML",
        description="Remove ML confidence gate (rule-based only)",
        flags={"ablation_ml": False},
    ),
    AblationTest(
        name="NO_SESSION",
        description="Remove session filter (trade all 24 hours)",
        flags={"ablation_session": False},
    ),
    AblationTest(
        name="SENTIMENT_ONLY",
        description="Sentiment alone — all other components disabled",
        flags={
            "ablation_rsi":   False,
            "ablation_bb_kc": False,
            "ablation_adx":   False,
            "ablation_sr":    False,
            "ablation_mtf":   False,
            # sentiment stays True
            "ablation_ml":    False,   # ML can't confirm what rule-based doesn't score
        },
    ),
]


# ── Engine factory that injects ablation flags ────────────────────────────────

class _AblatedBacktestEngine(BacktestEngine):
    """BacktestEngine subclass that builds ConfluenceEngine with ablation flags."""

    def __init__(self, initial_balance: float, ablation_flags: dict) -> None:
        super().__init__(initial_balance=initial_balance)
        self._ablation_flags = ablation_flags

    def run(self, instrument: str, timeframe: str = "H1") -> BacktestResults:
        """Override run() to inject ablation flags into ConfluenceEngine."""
        import asyncio as _asyncio
        import numpy as np
        from anchor.ml.xgb_classifier import XGBDirectionClassifier
        from anchor.ml.feature_engineer import FeatureEngineer
        from pathlib import Path
        from anchor.risk.weekend_guard import WeekendGuard as _WG
        from anchor.risk.holiday_calendar import is_holiday
        from anchor.backtesting.engine import ATR_MULTIPLIER_SL, ATR_MULTIPLIER_TP
        from anchor.backtesting.results import compute_results
        from anchor.signals.engine import SignalResult
        import structlog

        logger = structlog.get_logger(__name__)
        _wg = _WG()

        def _is_weekend(dt) -> bool:
            return _wg._is_close_time(dt)

        # Load ML if available (ablation_ml flag controls whether it's used)
        MODEL_DIR = Path("/app/models")
        ml_clf = None
        if self._ablation_flags.get("ablation_ml", True):
            path = MODEL_DIR / f"{instrument}_xgb.pkl"
            clf = XGBDirectionClassifier(model_path=path)
            if clf.load(path):
                ml_clf = clf

        data_cache: dict = {"H1": {}, "H4": {}, "D": {}}
        engine = ConfluenceEngine(
            data_cache=data_cache,
            ml_classifier=ml_clf,
            feature_engineer=FeatureEngineer() if ml_clf else None,
            **self._ablation_flags,
        )

        loop = _asyncio.new_event_loop()
        pending_fill: dict | None = None

        try:
            for bar in self.feed.stream(instrument, timeframe):
                # Fill pending signal at next bar open
                if pending_fill is not None:
                    if not (_is_weekend(bar.time) or is_holiday(bar.time)):
                        fill_price = bar.open
                        direction  = pending_fill["direction"]
                        atr        = pending_fill["atr"]
                        if direction == "LONG":
                            sl = fill_price - ATR_MULTIPLIER_SL * atr
                            tp = fill_price + ATR_MULTIPLIER_TP * atr
                        else:
                            sl = fill_price + ATR_MULTIPLIER_SL * atr
                            tp = fill_price - ATR_MULTIPLIER_TP * atr
                        sl_distance = abs(fill_price - sl)
                        if sl_distance >= 1e-8:
                            units = self.sizer.compute_units(
                                account_balance=pending_fill["account_balance"],
                                stop_distance=sl_distance,
                                instrument=instrument,
                            )
                            self.broker.open_position(
                                instrument=instrument,
                                direction=direction,
                                units=units,
                                fill_price=fill_price,
                                stop_loss=sl,
                                take_profit=tp,
                                fill_time=bar.time,
                                signal_context=pending_fill["signal_context"],
                            )
                    pending_fill = None

                if _is_weekend(bar.time) or is_holiday(bar.time):
                    continue

                h1_window = self.feed.get_window(instrument, "H1", bar.time, lookback=200)
                h4_window = self.feed.get_window(instrument, "H4", bar.time, lookback=100)
                d1_window = self.feed.get_window(instrument, "D",  bar.time, lookback=50)

                if h1_window is None or len(h1_window) < 60:
                    continue

                engine.update_cache(instrument, "H1", h1_window)
                if h4_window is not None:
                    engine.update_cache(instrument, "H4", h4_window)
                if d1_window is not None:
                    engine.update_cache(instrument, "D", d1_window)

                self.broker.update(bar)

                open_positions = [
                    p for p in self.broker.positions
                    if p.instrument == instrument and p.status == "OPEN"
                ]
                if open_positions:
                    continue

                try:
                    result: SignalResult = loop.run_until_complete(
                        engine.evaluate(instrument=instrument, dt=bar.time)
                    )
                except Exception as exc:
                    logger.debug("signal_error", bar=str(bar.time), error=str(exc))
                    continue

                if result.suppressed or result.direction is None:
                    continue

                closes     = h1_window["close"].values
                highs      = h1_window["high"].values
                lows       = h1_window["low"].values
                prev_c     = np.roll(closes, 1); prev_c[0] = closes[0]
                tr         = np.maximum(highs - lows, np.maximum(
                    np.abs(highs - prev_c), np.abs(lows - prev_c)))
                _p = 14
                atr = float(np.mean(tr[:_p]))
                _a  = 1.0 / _p
                for _v in tr[_p:]:
                    atr = _a * float(_v) + (1.0 - _a) * atr

                pending_fill = {
                    "direction":       result.direction,
                    "atr":             atr,
                    "account_balance": self.broker.account_balance,
                    "signal_context": {
                        "regime":           result.regime_state,
                        "session":          result.session,
                        "confluence_score": result.confluence_score,
                        "rsi_score":        result.rsi_score,
                        "bb_kc_score":      result.bb_kc_score,
                        "adx_score":        result.adx_score,
                        "sr_score":         result.sr_score,
                        "mtf_score":        result.mtf_score,
                        "csi_score":        result.csi_score,
                        "ml_confidence":    result.ml_confidence,
                        "atr":              atr,
                    },
                }
        finally:
            loop.close()

        return compute_results(self.broker, instrument, timeframe)


# ── Result container ──────────────────────────────────────────────────────────

@dataclass
class AblationResult:
    test: AblationTest
    results: BacktestResults


# ── Comparison table printer ──────────────────────────────────────────────────

def print_ablation_table(rows: list[AblationResult], instrument: str) -> None:
    W = "\033[92m"
    L = "\033[91m"
    N = "\033[0m"
    H = "\033[1m"

    baseline = rows[0].results

    def _delta(val: float, base: float, higher_is_better: bool = True) -> str:
        d = val - base
        if abs(d) < 1e-6:
            return ""
        col = W if (d > 0) == higher_is_better else L
        return f" {col}({d:+.2f}){N}"

    print(f"\n{'='*90}")
    print(f"{H}ABLATION STUDY — {instrument}{N}")
    print(f"{'='*90}")
    print(f"  {H}{'Test':<20} {'Trades':>7} {'WinRate':>8} {'ProfFact':>9} {'NetPnL%':>9} {'MaxDD%':>8} {'Sharpe':>8}{N}")
    print(f"  {'-'*85}")

    for r in rows:
        res  = r.results
        name = r.test.name
        tag  = f"{H}{'→':>2}{N} " if name == "BASELINE" else "   "

        wr_col   = W if res.win_rate   > 0.50 else L
        pf_col   = W if res.profit_factor > 1.0 else L
        pnl_col  = W if res.net_pnl_pct  > 0   else L
        dd_col   = W if res.max_drawdown_pct < 20 else L
        sh_col   = W if res.sharpe_ratio > 1.0  else L

        wr_d  = _delta(res.win_rate * 100,         baseline.win_rate * 100)
        pf_d  = _delta(res.profit_factor,           baseline.profit_factor)
        pnl_d = _delta(res.net_pnl_pct,             baseline.net_pnl_pct)
        dd_d  = _delta(res.max_drawdown_pct,        baseline.max_drawdown_pct, higher_is_better=False)
        sh_d  = _delta(res.sharpe_ratio,            baseline.sharpe_ratio)

        print(
            f"  {tag}{name:<20}"
            f" {res.total_trades:>7}"
            f" {wr_col}{res.win_rate*100:>7.1f}%{N}{wr_d}"
            f" {pf_col}{res.profit_factor:>8.2f}{N}{pf_d}"
            f" {pnl_col}{res.net_pnl_pct:>8.1f}%{N}{pnl_d}"
            f" {dd_col}{res.max_drawdown_pct:>7.1f}%{N}{dd_d}"
            f" {sh_col}{res.sharpe_ratio:>7.2f}{N}{sh_d}"
        )

    print(f"\n  {'Test':<20}  Description")
    print(f"  {'-'*70}")
    for r in rows:
        print(f"  {r.test.name:<20}  {r.test.description}")

    print(f"\n{H}INTERPRETATION GUIDE{N}")
    print("  If removing a component causes Sharpe to DROP   → it adds edge (keep it)")
    print("  If removing a component causes Sharpe to RISE   → it hurts edge (consider removing)")
    print("  SENTIMENT_ONLY Sharpe tells you the standalone value of the contrarian signal")
    print(f"{'='*90}\n")


# ── CSV export ────────────────────────────────────────────────────────────────

def export_csv(rows: list[AblationResult], instrument: str, path: str) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "instrument", "test", "description",
            "total_trades", "win_rate", "profit_factor",
            "net_pnl_pct", "max_drawdown_pct", "sharpe_ratio", "sortino_ratio",
        ])
        for r in rows:
            res = r.results
            writer.writerow([
                instrument,
                r.test.name,
                r.test.description,
                res.total_trades,
                round(res.win_rate, 4),
                round(res.profit_factor, 4),
                round(res.net_pnl_pct, 2),
                round(res.max_drawdown_pct, 2),
                round(res.sharpe_ratio, 4),
                round(res.sortino_ratio, 4),
            ])
    print(f"  Ablation results exported to: {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def _main() -> None:
    parser = argparse.ArgumentParser(description="Ablation study runner")
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--h1-csv",     required=True,  help="Path to H1 OHLCV CSV")
    parser.add_argument("--h4-csv",     default=None,   help="Path to H4 OHLCV CSV (optional)")
    parser.add_argument("--d-csv",      default=None,   help="Path to Daily OHLCV CSV (optional)")
    parser.add_argument("--balance",    type=float, default=10_000.0)
    parser.add_argument("--export-csv", default=None,   help="Save summary to CSV path")
    parser.add_argument("--tests",      default=None,
                        help="Comma-separated test names to run (default: all). "
                             "Options: BASELINE,NO_SENTIMENT,NO_HMM,NO_ML,NO_SESSION,SENTIMENT_ONLY")
    args = parser.parse_args()

    # Filter tests if requested
    selected_names = {t.strip().upper() for t in args.tests.split(",")} if args.tests else None
    tests_to_run = [
        t for t in ABLATION_TESTS
        if selected_names is None or t.name in selected_names
    ]
    if not tests_to_run:
        print("No matching tests found. Check --tests argument.")
        return

    def load(path: str | None) -> pd.DataFrame | None:
        if path is None:
            return None
        df = pd.read_csv(path, parse_dates=["time"])
        df["time"] = pd.to_datetime(df["time"], utc=True)
        return df.sort_values("time").reset_index(drop=True)

    h1 = load(args.h1_csv)
    h4 = load(args.h4_csv)
    d1 = load(args.d_csv)

    if h1 is None or len(h1) < 200:
        print("ERROR: H1 CSV has fewer than 200 bars. Cannot run ablation.")
        return

    ablation_results: list[AblationResult] = []

    for test in tests_to_run:
        print(f"\n[{test.name}] {test.description}")
        eng = _AblatedBacktestEngine(
            initial_balance=args.balance,
            ablation_flags=test.flags,
        )
        eng.load_df(args.instrument, "H1", h1)
        if h4 is not None:
            eng.load_df(args.instrument, "H4", h4)
        if d1 is not None:
            eng.load_df(args.instrument, "D", d1)

        results = eng.run(args.instrument, "H1")
        ablation_results.append(AblationResult(test=test, results=results))
        print(
            f"  → {results.total_trades} trades | "
            f"Sharpe {results.sharpe_ratio:.2f} | "
            f"WR {results.win_rate*100:.1f}% | "
            f"MaxDD {results.max_drawdown_pct:.1f}%"
        )

    print_ablation_table(ablation_results, args.instrument)

    if args.export_csv:
        export_csv(ablation_results, args.instrument, args.export_csv)


if __name__ == "__main__":
    _main()
