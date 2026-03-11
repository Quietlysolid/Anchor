"""Dukascopy historical tick data downloader and importer.

Downloads free tick data from Dukascopy in .bi5 (LZMA-compressed) format.
Converts to OHLCV candles and inserts into the database.

Usage: python -m anchor.data.dukascopy --instrument EUR_USD --start 2020-01-01
"""
from __future__ import annotations

import argparse
import asyncio
import io
import struct
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

import httpx
import lzma
import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

BASE_URL = "https://datafeed.dukascopy.com/datafeed"

# Pair name mapping: our format → Dukascopy format
PAIR_MAP = {
    "EUR_USD": "EURUSD",
    "GBP_USD": "GBPUSD",
    "USD_JPY": "USDJPY",
    "USD_CHF": "USDCHF",
    "AUD_USD": "AUDUSD",
    "NZD_USD": "NZDUSD",
    "USD_CAD": "USDCAD",
    "EUR_JPY": "EURJPY",
}

# Dukascopy stores prices as integers in 1/100000 units (5 decimal places).
# Multiply by 0.00001 for non-JPY pairs, 0.001 for JPY pairs (2 decimal places).
PIP_FACTORS = {
    "USDJPY": 0.001,
    "EURJPY": 0.001,
}
DEFAULT_PIP_FACTOR = 0.00001

TICK_STRUCT = struct.Struct(">IIIff")  # time_ms, ask, bid, ask_vol, bid_vol
TICK_SIZE = TICK_STRUCT.size  # 20 bytes


def _duka_url(instrument: str, dt: datetime) -> str:
    sym = PAIR_MAP.get(instrument, instrument.replace("_", ""))
    return (
        f"{BASE_URL}/{sym}/{dt.year:04d}/{dt.month - 1:02d}/{dt.day:02d}/{dt.hour:02d}h_ticks.bi5"
    )


def _decode_bi5(data: bytes, dt_hour: datetime, pip_factor: float) -> pd.DataFrame:
    """Decode a bi5 chunk into a DataFrame of ticks."""
    try:
        raw = lzma.decompress(data)
    except Exception:
        return pd.DataFrame()

    n = len(raw) // TICK_SIZE
    if n == 0:
        return pd.DataFrame()

    records = [TICK_STRUCT.unpack_from(raw, i * TICK_SIZE) for i in range(n)]
    ms_offsets, asks, bids, ask_vols, bid_vols = zip(*records)

    timestamps = [
        dt_hour + timedelta(milliseconds=int(ms)) for ms in ms_offsets
    ]
    asks = [a * pip_factor for a in asks]
    bids = [b * pip_factor for b in bids]
    mids = [(a + b) / 2 for a, b in zip(asks, bids)]

    return pd.DataFrame(
        {"time": timestamps, "bid": bids, "ask": asks, "mid": mids, "volume": ask_vols}
    )


def _ticks_to_candles(ticks: pd.DataFrame, timeframe: str = "H1") -> pd.DataFrame:
    """Resample tick data to OHLCV candles."""
    if ticks.empty:
        return pd.DataFrame()

    freq_map = {"M1": "1min", "M5": "5min", "M15": "15min", "H1": "1h", "H4": "4h", "D": "1D"}
    freq = freq_map.get(timeframe, "1h")

    ticks = ticks.set_index("time")
    ohlcv = ticks["mid"].resample(freq).ohlc()
    ohlcv["volume"] = ticks["volume"].resample(freq).sum()
    ohlcv["spread"] = (ticks["ask"] - ticks["bid"]).resample(freq).mean()
    ohlcv = ohlcv.dropna()
    ohlcv.index = ohlcv.index.tz_localize("UTC") if ohlcv.index.tz is None else ohlcv.index
    return ohlcv.reset_index()


class DukascopyDownloader:
    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "DukascopyDownloader":
        self._client = httpx.AsyncClient(timeout=60.0)
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    async def fetch_hour(self, instrument: str, dt: datetime) -> pd.DataFrame:
        """Fetch one hour of tick data."""
        assert self._client is not None
        url = _duka_url(instrument, dt)
        sym = PAIR_MAP.get(instrument, instrument.replace("_", ""))
        pip_factor = PIP_FACTORS.get(sym, DEFAULT_PIP_FACTOR)

        try:
            resp = await self._client.get(url)
            if resp.status_code == 404:
                return pd.DataFrame()
            resp.raise_for_status()
            return _decode_bi5(resp.content, dt.replace(minute=0, second=0, microsecond=0), pip_factor)
        except Exception as exc:
            logger.debug("dukascopy_hour_failed", url=url, error=str(exc))
            return pd.DataFrame()

    async def fetch_day(self, instrument: str, day: datetime) -> pd.DataFrame:
        """Fetch all 24 hours for a day concurrently."""
        hours = [day.replace(hour=h, minute=0, second=0, microsecond=0) for h in range(24)]
        tasks = [self.fetch_hour(instrument, h) for h in hours]
        frames = await asyncio.gather(*tasks)
        valid = [f for f in frames if not f.empty]
        if not valid:
            return pd.DataFrame()
        return pd.concat(valid, ignore_index=True).sort_values("time")

    async def fetch_range_as_candles(
        self,
        instrument: str,
        start: datetime,
        end: datetime,
        timeframe: str = "H1",
    ) -> pd.DataFrame:
        """Download a date range and return as OHLCV candles."""
        all_ticks: List[pd.DataFrame] = []
        current = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while current <= end:
            logger.info("dukascopy_fetch_day", instrument=instrument, date=current.date())
            day_ticks = await self.fetch_day(instrument, current)
            if not day_ticks.empty:
                all_ticks.append(day_ticks)
            current += timedelta(days=1)
            await asyncio.sleep(0.1)  # polite

        if not all_ticks:
            return pd.DataFrame()

        combined = pd.concat(all_ticks, ignore_index=True).sort_values("time")
        return _ticks_to_candles(combined, timeframe)


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Import Dukascopy history")
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--output", default=None,
                        help="Output CSV path (default: dukascopy_<PAIR>_<TF>.csv)")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = (
        datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.end
        else datetime.now(timezone.utc)
    )

    async with DukascopyDownloader() as dl:
        candles = await dl.fetch_range_as_candles(args.instrument, start, end, args.timeframe)

    logger.info("dukascopy_import_done", rows=len(candles), instrument=args.instrument)

    out = args.output or f"dukascopy_{args.instrument}_{args.timeframe}.csv"
    candles.to_csv(out, index=False)
    logger.info("saved", file=out)


if __name__ == "__main__":
    asyncio.run(_main())
