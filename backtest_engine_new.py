"""Event-driven backtesting engine.

Replays historical candle data through the same signal and risk pipeline
used in live trading. No lookahead — each bar only sees past data.

Usage: python -m anchor.backtesting.engine --instrument EUR_USD --start 2020-01-01
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

import asyncio as _asyncio

import numpy as np
import pandas as pd
import structlog

from pathlib import Path

from anchor.backtesting.data_feed import HistoricalDataFeed
from anchor.backtesting.simulated_broker import SimulatedBroker
from anchor.backtesting.results import compute_results, BacktestResults
from anchor.config import settings
from anchor.risk.position_sizer import PositionSizer
from anchor.risk.weekend_guard import WeekendGuard as _WG
from anchor.risk.holiday_calendar import is_holiday
from anchor.ml.xgb_classifier import XGBDirectionClassifier
from anchor.ml.feature_engineer import FeatureEngineer

MODEL_DIR = Path("/app/models")

_wg = _WG()

def is_weekend_close_time(dt) -> bool:
    return _wg._is_close_time(dt)
from anchor.signals.engine import ConfluenceEngine, SignalResult

logger = structlog.get_logger(__name__)

ATR_MULTIPLIER_SL = 1.5   # stop loss = 1.5x ATR
ATR_MULTIPLIER_TP = 1.5   # take profit = 1.5x ATR (1:1 R/R) — OOS data shows LONDON 56% WR → PF 1.29


def _load_ml_classifier(instrument: str) -> XGBDirectionClassifier | None:
    """Load a trained XGBoost model for the given instrument if it exists."""
    path = MODEL_DIR / f"{instrument}_xgb.pkl"
    clf = XGBDirectionClassifier(model_path=path)
    if clf.load(path):
        return clf
    return None


class BacktestEngine:
    def __init__(self, initial_balance: float = 10_000.0) -> None:
        self.broker = SimulatedBroker(account_balance=initial_balance)
        self.feed = HistoricalDataFeed()
        self.sizer = PositionSizer()
        self._feature_engineer = FeatureEngineer()

    def load_csv(self, instrument: str, timeframe: str, path: str) -> None:
        df = pd.read_csv(path, parse_dates=["time"])
        self.feed.load(instrument, timeframe, df)

    def load_df(self, instrument: str, timeframe: str, df: pd.DataFrame) -> None:
        self.feed.load(instrument, timeframe, df)

    def run(
        self,
        instrument: str,
        timeframe: str = "H1",
    ) -> BacktestResults:
        """Run the backtest and return results.

        The backtesting engine populates the ConfluenceEngine's data_cache
        directly (same mechanism used in live trading) and calls evaluate()
        with the correct signature: (instrument, dt).
        """
        # Load ML model if available
        ml_clf = _load_ml_classifier(instrument)
        if ml_clf:
            logger.info("ml_model_loaded", instrument=instrument)
        else:
            logger.info("ml_model_not_found_running_without_ml", instrument=instrument)

        # Build the engine with a data_cache dict that we update each bar
        data_cache: dict = {"H1": {}, "H4": {}, "D": {}}
        engine = ConfluenceEngine(
            data_cache=data_cache,
            ml_classifier=ml_clf,
            feature_engineer=self._feature_engineer if ml_clf else None,
        )

        # Create a new event loop for running async evaluate() calls
        loop = _asyncio.new_event_loop()

        bar_count = 0
        signal_count = 0

        # Pending fill: signal fired on the previous bar; fill at this bar's open.
        # Structure: dict with keys direction, sl_atr_offset, tp_atr_offset,
        # signal_context, units_basis (account_balance and stop_distance at signal time).
        pending_fill: dict | None = None

        try:
            for bar in self.feed.stream(instrument, timeframe):
                bar_count += 1

                # ── Fill pending signal at this bar's open (no lookahead) ────────
                if pending_fill is not None:
                    if not (is_weekend_close_time(bar.time) or is_holiday(bar.time)):
                        fill_price = bar.open
                        direction = pending_fill["direction"]
                        atr = pending_fill["atr"]
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

                # Skip weekends and holidays
                if is_weekend_close_time(bar.time) or is_holiday(bar.time):
                    continue

                # Populate data_cache with lookback windows — this is exactly
                # what the live stream does via update_cache()
                h1_window = self.feed.get_window(instrument, "H1", bar.time, lookback=200)
                h4_window = self.feed.get_window(instrument, "H4", bar.time, lookback=100)
                d1_window = self.feed.get_window(instrument, "D", bar.time, lookback=50)

                if h1_window is None or len(h1_window) < 60:
                    continue

                # Update the engine's cache (same interface as live)
                engine.update_cache(instrument, "H1", h1_window)
                if h4_window is not None:
                    engine.update_cache(instrument, "H4", h4_window)
                if d1_window is not None:
                    engine.update_cache(instrument, "D", d1_window)

                # Update broker on this bar (SL/TP checks)
                self.broker.update(bar)

                # Skip if already in a position on this instrument
                open_on_instrument = [
                    p for p in self.broker.positions
                    if p.instrument == instrument and p.status == "OPEN"
                ]
                if open_on_instrument:
                    continue

                # Evaluate confluence using the correct signature
                try:
                    result: SignalResult = loop.run_until_complete(
                        engine.evaluate(instrument=instrument, dt=bar.time)
                    )
                except Exception as exc:
                    logger.debug("signal_error", bar=str(bar.time), error=str(exc))
                    continue

                if result.suppressed or result.direction is None:
                    continue

                signal_count += 1

                # Compute ATR on this bar's window (no lookahead).
                # Uses Wilder's smoothing (EMA α=1/14) to match the `ta` library
                # used in live signal generation — preventing SL/TP divergence
                # between backtest and live execution in volatile periods.
                closes = h1_window["close"].values
                highs = h1_window["high"].values
                lows = h1_window["low"].values
                prev_closes = np.roll(closes, 1)
                prev_closes[0] = closes[0]  # avoid roll wrap artifact
                tr = np.maximum(
                    highs - lows,
                    np.maximum(
                        np.abs(highs - prev_closes),
                        np.abs(lows - prev_closes),
                    ),
                )
                # Wilder's: seed with 14-bar mean, then apply EMA with α=1/14
                _atr_period = 14
                atr = float(np.mean(tr[:_atr_period]))
                _alpha = 1.0 / _atr_period
                for _tr_val in tr[_atr_period:]:
                    atr = _alpha * float(_tr_val) + (1.0 - _alpha) * atr

                # Queue fill for next bar's open — no lookahead on price.
                pending_fill = {
                    "direction": result.direction,
                    "atr": atr,
                    "account_balance": self.broker.account_balance,
                    "signal_context": {
                        "regime": result.regime_state,
                        "session": result.session,
                        "confluence_score": result.confluence_score,
                        "rsi_score": result.rsi_score,
                        "bb_kc_score": result.bb_kc_score,
                        "adx_score": result.adx_score,
                        "sr_score": result.sr_score,
                        "mtf_score": result.mtf_score,
                        "csi_score": result.csi_score,
                        "ml_confidence": result.ml_confidence,
                        "atr": atr,
                    },
                }
        finally:
            loop.close()

        logger.info(
            "backtest_complete",
            instrument=instrument,
            bars=bar_count,
            signals=signal_count,
            trades=self.broker.total_trades,
            final_balance=round(self.broker.account_balance, 2),
        )

        return compute_results(self.broker, instrument, timeframe)


def analyze_trade_log(trade_log: list, output_csv: str | None = None) -> None:
    """Print a surgical breakdown of the trade log by regime, direction, session, and score bands.

    Call after run() to diagnose *why* the system wins or loses.
    Optionally saves the full enriched log to a CSV for further analysis.
    """
    if not trade_log:
        print("No trades to analyze.")
        return

    df = pd.DataFrame(trade_log)

    W = "\033[92m"   # green
    L = "\033[91m"   # red
    N = "\033[0m"    # reset
    H = "\033[1m"    # bold

    def _fmt_wr(wins, total):
        if total == 0:
            return "  n/a"
        wr = wins / total
        col = W if wr >= 0.5 else L
        return f"{col}{wr*100:5.1f}%{N}"

    def _section(title: str, group_col: str) -> None:
        print(f"\n{H}{title}{N}")
        print(f"  {'Category':<18} {'Trades':>6}  {'WR':>7}  {'Avg P&L':>9}  {'Total P&L':>10}")
        print(f"  {'-'*18}  {'-'*6}  {'-'*7}  {'-'*9}  {'-'*10}")
        for cat, g in df.groupby(group_col, dropna=False):
            n = len(g)
            wins = (g["outcome"] == 1).sum()
            avg_pl = g["net_pl"].mean()
            tot_pl = g["net_pl"].sum()
            wr_str = _fmt_wr(wins, n)
            pl_col = W if avg_pl > 0 else L
            print(f"  {str(cat):<18} {n:>6}  {wr_str}  {pl_col}{avg_pl:>+9.2f}{N}  {pl_col}{tot_pl:>+10.2f}{N}")

    # ── Summary ──────────────────────────────────────────────────────────────
    total = len(df)
    wins = (df["outcome"] == 1).sum()
    print(f"\n{'='*60}")
    print(f"{H}TRADE LOG DIAGNOSTIC — {df['instrument'].iloc[0]} ({total} trades){N}")
    print(f"{'='*60}")
    print(f"  Overall Win Rate: {_fmt_wr(wins, total)}  |  Net P&L: {df['net_pl'].sum():+.2f}")

    # ── 1. Regime breakdown ──────────────────────────────────────────────────
    _section("1. BY REGIME  (is it trading in wrong regime?)", "regime")

    # ── 2. Direction breakdown ───────────────────────────────────────────────
    _section("2. BY DIRECTION  (directional bias?)", "direction")

    # ── 3. Session breakdown ─────────────────────────────────────────────────
    _section("3. BY SESSION  (timing edge?)", "session")

    # ── 4. Regime × Direction (most informative cross) ───────────────────────
    print(f"\n{H}4. REGIME × DIRECTION  (worst buckets){N}")
    print(f"  {'Regime':<12} {'Dir':<6} {'Trades':>6}  {'WR':>7}  {'Avg P&L':>9}  {'Total P&L':>10}")
    print(f"  {'-'*12}  {'-'*6}  {'-'*6}  {'-'*7}  {'-'*9}  {'-'*10}")
    for (regime, direction), g in df.groupby(["regime", "direction"], dropna=False):
        n = len(g)
        wins_n = (g["outcome"] == 1).sum()
        avg_pl = g["net_pl"].mean()
        tot_pl = g["net_pl"].sum()
        wr_str = _fmt_wr(wins_n, n)
        pl_col = W if avg_pl > 0 else L
        print(f"  {str(regime):<12}  {str(direction):<6} {n:>6}  {wr_str}  {pl_col}{avg_pl:>+9.2f}{N}  {pl_col}{tot_pl:>+10.2f}{N}")

    # ── 5. Confluence score bands ─────────────────────────────────────────────
    print(f"\n{H}5. BY CONFLUENCE SCORE BAND  (does higher score = better outcome?){N}")
    bins = [0.0, 0.65, 0.70, 0.75, 0.80, 0.85, 1.01]
    labels = ["<0.65", "0.65-0.70", "0.70-0.75", "0.75-0.80", "0.80-0.85", "≥0.85"]
    df["conf_band"] = pd.cut(df["confluence_score"], bins=bins, labels=labels, right=False)
    print(f"  {'Band':<12} {'Trades':>6}  {'WR':>7}  {'Avg P&L':>9}  {'Total P&L':>10}")
    print(f"  {'-'*12}  {'-'*6}  {'-'*7}  {'-'*9}  {'-'*10}")
    for band, g in df.groupby("conf_band", observed=True):
        n = len(g)
        wins_n = (g["outcome"] == 1).sum()
        avg_pl = g["net_pl"].mean()
        tot_pl = g["net_pl"].sum()
        wr_str = _fmt_wr(wins_n, n)
        pl_col = W if avg_pl > 0 else L
        print(f"  {str(band):<12} {n:>6}  {wr_str}  {pl_col}{avg_pl:>+9.2f}{N}  {pl_col}{tot_pl:>+10.2f}{N}")

    # ── 6. Component score correlations with outcome ──────────────────────────
    print(f"\n{H}6. COMPONENT SCORE → WIN CORRELATION  (which scores predict wins?){N}")
    score_cols = ["confluence_score", "rsi_score", "bb_kc_score", "adx_score",
                  "sr_score", "mtf_score", "csi_score"]
    corr_rows = []
    for col in score_cols:
        if df[col].std() > 0:
            c = df[col].corr(df["outcome"])
            corr_rows.append((col, c))
    corr_rows.sort(key=lambda x: abs(x[1]), reverse=True)
    print(f"  {'Component':<20} {'Corr with Win':>14}")
    print(f"  {'-'*20}  {'-'*14}")
    for col, c in corr_rows:
        col_str = W if c > 0 else L
        print(f"  {col:<20}  {col_str}{c:>+14.4f}{N}")

    # ── 7. ML confidence bands (if present) ──────────────────────────────────
    if df["ml_confidence"].notna().sum() > 10:
        print(f"\n{H}7. ML CONFIDENCE BANDS  (calibration check){N}")
        ml_bins = [0.0, 0.55, 0.60, 0.65, 0.70, 0.80, 1.01]
        ml_labels = ["<0.55", "0.55-0.60", "0.60-0.65", "0.65-0.70", "0.70-0.80", "≥0.80"]
        df["ml_band"] = pd.cut(df["ml_confidence"], bins=ml_bins, labels=ml_labels, right=False)
        print(f"  {'ML Band':<12} {'Trades':>6}  {'WR':>7}  {'Avg P&L':>9}")
        print(f"  {'-'*12}  {'-'*6}  {'-'*7}  {'-'*9}")
        for band, g in df.groupby("ml_band", observed=True):
            n = len(g)
            wins_n = (g["outcome"] == 1).sum()
            avg_pl = g["net_pl"].mean()
            wr_str = _fmt_wr(wins_n, n)
            pl_col = W if avg_pl > 0 else L
            print(f"  {str(band):<12} {n:>6}  {wr_str}  {pl_col}{avg_pl:>+9.2f}{N}")

    # ── 8. Worst 10 trades ────────────────────────────────────────────────────
    print(f"\n{H}8. WORST 10 TRADES  (common traits of losers){N}")
    worst = df.nsmallest(10, "net_pl")[
        ["entry_time", "direction", "regime", "session", "confluence_score", "net_pl", "close_reason"]
    ]
    print(worst.to_string(index=False))

    # ── CSV export ────────────────────────────────────────────────────────────
    if output_csv:
        df.drop(columns=["conf_band"], errors="ignore").to_csv(output_csv, index=False)
        print(f"\n  Full trade log saved to: {output_csv}")

    print(f"\n{'='*60}\n")


def _main() -> None:
    parser = argparse.ArgumentParser(description="Run backtest")
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--csv", required=True, help="Path to OHLCV CSV")
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--analyze", action="store_true", help="Print surgical trade log analysis after run")
    parser.add_argument("--export-csv", default=None, help="Save enriched trade log to this CSV path")
    args = parser.parse_args()

    eng = BacktestEngine(initial_balance=args.balance)
    eng.load_csv(args.instrument, args.timeframe, args.csv)
    results = eng.run(args.instrument, args.timeframe)

    print(f"\n{'='*60}")
    print(f"BACKTEST RESULTS — {results.instrument} {results.timeframe}")
    print(f"{'='*60}")
    print(f"Period:           {results.start_date} → {results.end_date}")
    print(f"Total Trades:     {results.total_trades}")
    print(f"Win Rate:         {results.win_rate * 100:.1f}%")
    print(f"Profit Factor:    {results.profit_factor:.2f}")
    print(f"Net P&L:          ${results.net_pnl:.2f} ({results.net_pnl_pct:.1f}%)")
    print(f"Max Drawdown:     {results.max_drawdown_pct:.1f}%")
    print(f"Sharpe Ratio:     {results.sharpe_ratio:.2f}")
    print(f"Sortino Ratio:    {results.sortino_ratio:.2f}")
    print(f"Avg Win/Loss:     ${results.avg_win_pips:.2f} / ${results.avg_loss_pips:.2f}")

    if args.analyze or args.export_csv:
        analyze_trade_log(results.trade_log, output_csv=args.export_csv)


if __name__ == "__main__":
    _main()
