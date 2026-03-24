"""London trend rescue matrix.

Test the few structural levers that can plausibly revive the sleeve:
- threshold
- HMM gate on/off
- entry mode: pullback limit vs immediate market

This is intentionally small. The goal is not parameter mining; it is to learn
whether the sleeve is failing because of:
1. no signals,
2. no fills,
3. weak post-entry edge.
"""
from __future__ import annotations

import argparse
import logging
from typing import Any

import pandas as pd

from anchor.backtesting.ablation_runner import _AblatedBacktestEngine
from anchor.backtesting.engine import BacktestEngine, _set_time_index
from anchor.backtesting.results import compute_results
from anchor.config import settings
from anchor.risk.holiday_calendar import is_holiday
from anchor.risk.weekend_guard import WeekendGuard as _WG
from anchor.utils.math_utils import wilder_atr_scalar

logging.disable(logging.CRITICAL)
_wg = _WG()


def _load_df(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


class _MarketEntryBacktestEngine(BacktestEngine):
    """Immediate-entry variant for testing whether pullback limits are the choke point."""

    def __init__(self, initial_balance: float, confluence_threshold: float, ablation_flags: dict | None = None) -> None:
        super().__init__(initial_balance=initial_balance)
        self._threshold = confluence_threshold
        self._ablation_flags = ablation_flags or {}

    def run(self, instrument: str, timeframe: str = "H1"):
        import asyncio as _asyncio

        from anchor.backtesting.engine import _load_ml_classifier
        from anchor.regime.atr_classifier import AtrRegimeClassifier
        from anchor.signals.engine import ConfluenceEngine, SignalResult

        atr_classifier = AtrRegimeClassifier()
        ml_clf = _load_ml_classifier(instrument)
        data_cache: dict = {"H1": {}, "H4": {}, "D": {}}
        engine = ConfluenceEngine(
            data_cache=data_cache,
            ml_classifier=ml_clf,
            feature_engineer=self._feature_engineer if ml_clf else None,
            hmm_detector=atr_classifier,
            confluence_threshold=self._threshold,
            ablation_sentiment=False,
            ablation_rate_divergence=False,
            ablation_order_book=False,
            ablation_cme_flow=False,
            ablation_fx_options=False,
            ablation_econ_surprise=False,
            ablation_cross_asset=False,
            **self._ablation_flags,
        )

        loop = _asyncio.new_event_loop()
        try:
            for bar in self.feed.stream(instrument, timeframe):
                if _wg._is_close_time(bar.time) or is_holiday(bar.time):
                    continue

                h1_window = self.feed.get_window(instrument, "H1", bar.time, lookback=200)
                h4_window = self.feed.get_window(instrument, "H4", bar.time, lookback=100)
                d1_window = self.feed.get_window(instrument, "D", bar.time, lookback=150)
                if h1_window is None or len(h1_window) < 60:
                    continue

                engine.update_cache(instrument, "H1", _set_time_index(h1_window))
                if h4_window is not None:
                    engine.update_cache(instrument, "H4", _set_time_index(h4_window))
                if d1_window is not None:
                    engine.update_cache(instrument, "D", _set_time_index(d1_window))

                self.broker.update(bar)
                open_on_instrument = [
                    p for p in self.broker.positions
                    if p.instrument == instrument and p.status == "OPEN"
                ]
                if open_on_instrument:
                    continue

                result: SignalResult = loop.run_until_complete(engine.evaluate(instrument=instrument, dt=bar.time))
                if result.suppressed or result.direction is None:
                    continue

                atr = wilder_atr_scalar(
                    h1_window["high"].to_numpy(dtype=float),
                    h1_window["low"].to_numpy(dtype=float),
                    h1_window["close"].to_numpy(dtype=float),
                )
                if atr <= 0:
                    continue

                close_price = float(bar.close)
                if result.direction == "LONG":
                    entry = close_price
                    stop_loss = round(entry - 1.5 * atr, 5)
                    take_profit = round(entry + 2.0 * atr, 5)
                else:
                    entry = close_price
                    stop_loss = round(entry + 1.5 * atr, 5)
                    take_profit = round(entry - 2.0 * atr, 5)

                stop_distance = abs(entry - stop_loss)
                units = self.sizer.compute_units(
                    account_balance=self.broker.account_balance,
                    stop_distance=stop_distance,
                    instrument=instrument,
                )
                if units <= 0:
                    continue

                self.broker.open_position(
                    instrument=instrument,
                    direction=result.direction,
                    units=units,
                    fill_price=entry,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    fill_time=bar.time,
                    signal_context={
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
                )
        finally:
            loop.close()

        return compute_results(self.broker, instrument, timeframe)


def _slice_with_warmup(df: pd.DataFrame, start: str | None, end: str | None, warmup: int = 200) -> pd.DataFrame:
    out = df
    if start:
        start_ts = pd.Timestamp(start, tz="UTC")
        pre = df[df["time"] < start_ts].tail(warmup)
        out = pd.concat([pre, df[df["time"] >= start_ts]], ignore_index=True)
    if end:
        end_ts = pd.Timestamp(end, tz="UTC")
        out = out[out["time"] <= end_ts]
    return out.reset_index(drop=True)


def run_variant(pair: str, variant: str, threshold: float, data_dir: str, start: str, end: str, balance: float) -> dict[str, Any]:
    h1 = _load_df(f"{data_dir}/{pair}_H1.csv")
    h4 = _load_df(f"{data_dir}/{pair}_H4.csv")
    d1 = _load_df(f"{data_dir}/{pair}_D.csv")
    h1 = _slice_with_warmup(h1, start, end)
    h4 = _slice_with_warmup(h4, start, end, warmup=100)
    d1 = _slice_with_warmup(d1, start, end, warmup=365)

    if variant == "baseline":
        eng = _AblatedBacktestEngine(initial_balance=balance, ablation_flags={})
        object.__setattr__(settings, "min_confluence_score", threshold)
    elif variant == "no_hmm":
        eng = _AblatedBacktestEngine(initial_balance=balance, ablation_flags={"ablation_hmm_gate": False})
        object.__setattr__(settings, "min_confluence_score", threshold)
    elif variant == "market_entry":
        eng = _MarketEntryBacktestEngine(initial_balance=balance, confluence_threshold=threshold)
    elif variant == "market_no_hmm":
        eng = _MarketEntryBacktestEngine(
            initial_balance=balance,
            confluence_threshold=threshold,
            ablation_flags={"ablation_hmm_gate": False},
        )
    else:
        raise ValueError(variant)

    eng.load_df(pair, "H1", h1)
    eng.load_df(pair, "H4", h4)
    eng.load_df(pair, "D", d1)
    res = eng.run(pair, "H1")
    return {
        "pair": pair,
        "variant": variant,
        "threshold": threshold,
        "trades": res.total_trades,
        "win_rate": res.win_rate * 100,
        "profit_factor": res.profit_factor,
        "net_pct": res.net_pnl_pct,
        "max_dd": res.max_drawdown_pct,
    }


def _print(rows: list[dict[str, Any]]) -> None:
    print(f"\n{'=' * 96}")
    print("LONDON TREND RESCUE MATRIX")
    print(f"{'=' * 96}")
    print(f"{'Pair':<10} {'Variant':<16} {'Thr':>5} {'Trades':>7} {'WR':>7} {'PF':>8} {'Net%':>8} {'MaxDD%':>8}")
    for row in rows:
        pf = row["profit_factor"]
        pf_str = "inf" if pf == float("inf") else f"{pf:.3f}"
        print(
            f"{row['pair']:<10} {row['variant']:<16} {row['threshold']:>5.2f} {row['trades']:>7} "
            f"{row['win_rate']:>6.1f}% {pf_str:>8} {row['net_pct']:>+7.2f} {row['max_dd']:>7.2f}"
        )
    print(f"{'=' * 96}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="London trend rescue matrix")
    parser.add_argument("--pairs", default=",".join(settings.trend_instruments))
    parser.add_argument("--data-dir", default="/app/data")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--thresholds", default="0.55,0.60,0.65,0.76")
    args = parser.parse_args()

    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    thresholds = [float(t.strip()) for t in args.thresholds.split(",") if t.strip()]
    variants = ["baseline", "no_hmm", "market_entry", "market_no_hmm"]
    rows = []
    for pair in pairs:
        for threshold in thresholds:
            for variant in variants:
                rows.append(run_variant(pair, variant, threshold, args.data_dir, args.start, args.end, args.balance))
    _print(rows)


if __name__ == "__main__":
    main()
