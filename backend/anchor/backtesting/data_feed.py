"""Historical data feed iterator for the backtesting engine.

Yields one candle at a time in chronological order, simulating live feed.
Supports multi-instrument, multi-timeframe lookback windows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Generator, Optional

import pandas as pd


@dataclass
class CandleBar:
    time: datetime
    instrument: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    spread: float = 0.0


class HistoricalDataFeed:
    """Provides an event-driven candle stream from a pre-loaded DataFrame."""

    def __init__(self) -> None:
        self._feeds: Dict[str, Dict[str, pd.DataFrame]] = {}  # instrument → TF → df

    def load(self, instrument: str, timeframe: str, df: pd.DataFrame) -> None:
        """Load a candle DataFrame. Required columns: time, open, high, low, close, volume."""
        required = {"time", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing columns: {missing}")

        df = df.copy()
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.sort_values("time").reset_index(drop=True)

        if instrument not in self._feeds:
            self._feeds[instrument] = {}
        self._feeds[instrument][timeframe] = df

    def get_window(
        self,
        instrument: str,
        timeframe: str,
        current_time: datetime,
        lookback: int = 200,
    ) -> Optional[pd.DataFrame]:
        """Return the last `lookback` candles strictly before `current_time`."""
        df = self._feeds.get(instrument, {}).get(timeframe)
        if df is None:
            return None
        mask = df["time"] < current_time
        past = df[mask].tail(lookback)
        return past if len(past) >= 20 else None

    def stream(
        self,
        primary_instrument: str,
        primary_timeframe: str,
    ) -> Generator[CandleBar, None, None]:
        """Yield candles chronologically for the primary instrument+timeframe."""
        df = self._feeds.get(primary_instrument, {}).get(primary_timeframe)
        if df is None:
            return

        for _, row in df.iterrows():
            yield CandleBar(
                time=row["time"].to_pydatetime(),
                instrument=primary_instrument,
                timeframe=primary_timeframe,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                spread=float(row.get("spread", 0.0)),
            )
