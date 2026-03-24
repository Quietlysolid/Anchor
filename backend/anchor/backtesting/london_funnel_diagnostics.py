"""London trend funnel diagnostics.

This script answers a practical question:
where does the London trend sleeve die?

For each pair it measures the full funnel:
- H1 bar evaluations
- generated signals
- pending limit orders submitted
- limit orders filled
- expired / cancelled orders
- resulting trades

It also reports the dominant suppression reasons and fill ratio so the user can
separate "no signal edge" from "signal exists but pullback-entry is too strict".
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from anchor.backtesting.engine import (
    BacktestEngine,
    _build_trend_limit_order,
    _limit_order_fill_price,
    _load_ml_classifier,
    _set_time_index,
)
from anchor.config import settings
from anchor.data.historical_cot import HistoricalCotDatabase
from anchor.regime.atr_classifier import AtrRegimeClassifier
from anchor.risk.holiday_calendar import is_holiday
from anchor.risk.weekend_guard import WeekendGuard as _WG
from anchor.signals.engine import ConfluenceEngine, SignalResult

logging.disable(logging.CRITICAL)

DATA_DIR = Path("/app/data")
_wg = _WG()
_BACKTEST_THRESHOLD = 0.76


class CotRedisMock:
    def __init__(self, cot_db: HistoricalCotDatabase) -> None:
        self._cot_db = cot_db
        self._bar_time = None

    def set_bar_time(self, dt) -> None:
        self._bar_time = dt

    async def get(self, key: str):
        if key == "cot_data" and self._bar_time is not None:
            return self._cot_db.as_redis_json(self._bar_time)
        return None


def is_weekend_close_time(dt) -> bool:
    return _wg._is_close_time(dt)


def _load_df(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def run_pair_funnel(
    pair: str,
    data_dir: Path,
    initial_balance: float,
    start: str | None,
    end: str | None,
) -> dict[str, Any]:
    h1 = data_dir / f"{pair}_H1.csv"
    h4 = data_dir / f"{pair}_H4.csv"
    d1 = data_dir / f"{pair}_D.csv"
    if not (h1.exists() and h4.exists() and d1.exists()):
        return {"pair": pair, "error": "missing_csv"}

    df_h1 = _load_df(h1)
    df_h4 = _load_df(h4)
    df_d1 = _load_df(d1)

    if start:
        start_ts = pd.Timestamp(start, tz="UTC")
        pre_h1 = df_h1[df_h1["time"] < start_ts].tail(200)
        df_h1 = pd.concat([pre_h1, df_h1[df_h1["time"] >= start_ts]], ignore_index=True)
        df_h4 = df_h4[df_h4["time"] >= start_ts - pd.Timedelta(days=120)]
        df_d1 = df_d1[df_d1["time"] >= start_ts - pd.Timedelta(days=365)]
    if end:
        end_ts = pd.Timestamp(end, tz="UTC")
        df_h1 = df_h1[df_h1["time"] <= end_ts]
        df_h4 = df_h4[df_h4["time"] <= end_ts]
        df_d1 = df_d1[df_d1["time"] <= end_ts]

    eng = BacktestEngine(initial_balance=initial_balance)
    eng.load_df(pair, "H1", df_h1)
    eng.load_df(pair, "H4", df_h4)
    eng.load_df(pair, "D", df_d1)

    atr_classifier = AtrRegimeClassifier()
    cot_db = HistoricalCotDatabase()
    cot_mock = None
    try:
        n_records = cot_db.load_years(2018, pd.Timestamp.utcnow().year)
        if n_records > 0:
            cot_mock = CotRedisMock(cot_db)
    except Exception:
        cot_mock = None

    ml_clf = _load_ml_classifier(pair)
    data_cache: dict = {"H1": {}, "H4": {}, "D": {}}
    engine = ConfluenceEngine(
        data_cache=data_cache,
        ml_classifier=ml_clf,
        feature_engineer=eng._feature_engineer if ml_clf else None,
        hmm_detector=atr_classifier,
        redis_client=cot_mock,
        confluence_threshold=_BACKTEST_THRESHOLD,
        ablation_sentiment=False,
        ablation_rate_divergence=False,
        ablation_order_book=False,
        ablation_cme_flow=False,
        ablation_fx_options=False,
        ablation_econ_surprise=False,
        ablation_cross_asset=False,
    )

    loop = asyncio.new_event_loop()
    pending_order: dict | None = None
    eval_count = 0
    signal_count = 0
    order_submit_count = 0
    fill_count = 0
    expiry_count = 0
    suppression = Counter()

    try:
        for bar in eng.feed.stream(pair, "H1"):
            if pending_order is not None:
                if bar.time > pending_order["expires_at"] or is_weekend_close_time(bar.time) or is_holiday(bar.time):
                    expiry_count += 1
                    pending_order = None
                else:
                    fill_price = _limit_order_fill_price(
                        pending_order["direction"],
                        pending_order["limit_price"],
                        bar,
                    )
                    if fill_price is not None:
                        eng.broker.open_position(
                            instrument=pair,
                            direction=pending_order["direction"],
                            units=pending_order["units"],
                            fill_price=fill_price,
                            stop_loss=pending_order["stop_loss"],
                            take_profit=pending_order["take_profit"],
                            fill_time=bar.time,
                            signal_context=pending_order["signal_context"],
                        )
                        fill_count += 1
                        pending_order = None

            if is_weekend_close_time(bar.time) or is_holiday(bar.time):
                continue

            h1_window = eng.feed.get_window(pair, "H1", bar.time, lookback=200)
            h4_window = eng.feed.get_window(pair, "H4", bar.time, lookback=100)
            d1_window = eng.feed.get_window(pair, "D", bar.time, lookback=150)
            if h1_window is None or len(h1_window) < 60:
                continue

            engine.update_cache(pair, "H1", _set_time_index(h1_window))
            if h4_window is not None:
                engine.update_cache(pair, "H4", _set_time_index(h4_window))
            if d1_window is not None:
                engine.update_cache(pair, "D", _set_time_index(d1_window))

            eng.broker.update(bar)

            open_on_instrument = [
                p for p in eng.broker.positions if p.instrument == pair and p.status == "OPEN"
            ]
            if open_on_instrument:
                continue

            if cot_mock is not None:
                cot_mock.set_bar_time(bar.time)

            result: SignalResult = loop.run_until_complete(engine.evaluate(instrument=pair, dt=bar.time))
            eval_count += 1

            if result.suppressed or result.direction is None:
                suppression[result.suppression_reason or "UNKNOWN"] += 1
                continue

            signal_count += 1
            pending_order = _build_trend_limit_order(
                h1_window=h1_window,
                direction=result.direction,
                account_balance=eng.broker.account_balance,
                instrument=pair,
                sizer=eng.sizer,
                submitted_at=bar.time,
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
                },
            )
            if pending_order is not None:
                order_submit_count += 1
            else:
                suppression["ORDER_BUILD_FAILED"] += 1

        if pending_order is not None:
            expiry_count += 1

    finally:
        loop.close()

    trades = eng.broker.trade_history
    top_reasons = suppression.most_common(12)
    return {
        "pair": pair,
        "evaluations": eval_count,
        "signals": signal_count,
        "orders_submitted": order_submit_count,
        "fills": fill_count,
        "expiries": expiry_count,
        "trades": len(trades),
        "fill_rate_vs_signals": (fill_count / signal_count * 100.0) if signal_count else 0.0,
        "trade_rate_vs_fills": (len(trades) / fill_count * 100.0) if fill_count else 0.0,
        "top_suppressions": top_reasons,
    }


def _print(results: list[dict[str, Any]]) -> None:
    print(f"\n{'=' * 108}")
    print("LONDON TREND FUNNEL DIAGNOSTICS")
    print(f"{'=' * 108}")
    print(
        f"{'Pair':<10} {'Evals':>8} {'Signals':>8} {'Orders':>8} {'Fills':>8} "
        f"{'Trades':>8} {'Fill/Sig':>10} {'Trade/Fill':>11}"
    )
    for row in results:
        if row.get("error"):
            print(f"{row['pair']:<10} ERROR {row['error']}")
            continue
        print(
            f"{row['pair']:<10} {row['evaluations']:>8} {row['signals']:>8} {row['orders_submitted']:>8} "
            f"{row['fills']:>8} {row['trades']:>8} {row['fill_rate_vs_signals']:>9.1f}% "
            f"{row['trade_rate_vs_fills']:>10.1f}%"
        )

    print(f"\nTOP SUPPRESSIONS")
    for row in results:
        if row.get("error"):
            continue
        reasons = ", ".join(f"{reason}:{count}" for reason, count in row["top_suppressions"][:6])
        print(f"{row['pair']:<10} {reasons}")
    print(f"{'=' * 108}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="London trend funnel diagnostics")
    parser.add_argument("--pairs", default=",".join(settings.trend_instruments))
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2025-12-31")
    args = parser.parse_args()

    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    results = [
        run_pair_funnel(pair, Path(args.data_dir), args.balance, args.start, args.end)
        for pair in pairs
    ]
    _print(results)


if __name__ == "__main__":
    main()
