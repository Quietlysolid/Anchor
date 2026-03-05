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

from anchor.backtesting.data_feed import HistoricalDataFeed
from anchor.backtesting.simulated_broker import SimulatedBroker
from anchor.backtesting.results import compute_results, BacktestResults
from anchor.config import settings
from anchor.risk.position_sizer import PositionSizer
from anchor.risk.weekend_guard import is_weekend_close_time
from anchor.risk.holiday_calendar import is_holiday
from anchor.signals.engine import ConfluenceEngine, SignalResult

logger = structlog.get_logger(__name__)

ATR_MULTIPLIER_SL = 1.5   # stop loss = 1.5x ATR
ATR_MULTIPLIER_TP = 3.0   # take profit = 3.0x ATR (2:1 R/R minimum)


class BacktestEngine:
    def __init__(self, initial_balance: float = 10_000.0) -> None:
        self.broker = SimulatedBroker(account_balance=initial_balance)
        self.feed = HistoricalDataFeed()
        self.sizer = PositionSizer()

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
        # Build the engine with a data_cache dict that we update each bar
        data_cache: dict = {"H1": {}, "H4": {}, "D": {}}
        engine = ConfluenceEngine(data_cache=data_cache)

        # Create a new event loop for running async evaluate() calls
        loop = _asyncio.new_event_loop()

        bar_count = 0
        signal_count = 0

        try:
            for bar in self.feed.stream(instrument, timeframe):
                bar_count += 1

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

                # Compute ATR-based SL/TP (no lookahead: use window data only)
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
                atr = float(np.mean(tr[-14:]))

                if result.direction == "LONG":
                    sl = bar.close - ATR_MULTIPLIER_SL * atr
                    tp = bar.close + ATR_MULTIPLIER_TP * atr
                else:
                    sl = bar.close + ATR_MULTIPLIER_SL * atr
                    tp = bar.close - ATR_MULTIPLIER_TP * atr

                sl_distance = abs(bar.close - sl)
                if sl_distance < 1e-8:
                    continue  # degenerate bar, skip

                units = self.sizer.compute_units(
                    account_balance=self.broker.account_balance,
                    stop_distance=sl_distance,
                    instrument=instrument,
                )

                self.broker.open_position(
                    instrument=instrument,
                    direction=result.direction,
                    units=units,
                    fill_price=bar.close,
                    stop_loss=sl,
                    take_profit=tp,
                    fill_time=bar.time,
                )
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


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Run backtest")
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--csv", required=True, help="Path to OHLCV CSV")
    parser.add_argument("--balance", type=float, default=10_000.0)
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


if __name__ == "__main__":
    asyncio.run(_main())
