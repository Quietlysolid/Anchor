"""Polygon.io historical forex data fetcher.

Fetches OHLCV candles from the Polygon aggregate bars API and inserts them
into the database.  Drop-in replacement for the Dukascopy downloader with
no LZMA parsing, no 503 errors, and support for any timeframe in one call.

Usage (inside engine container):
    python -m anchor.data.polygon --instrument EUR_USD --timeframe H1
    python -m anchor.data.polygon --instrument USD_CAD --start 2022-01-01 --end 2024-12-31
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

BASE_URL = "https://api.polygon.io/v2/aggs/ticker"

# Our format → Polygon forex ticker
PAIR_MAP = {
    "EUR_USD": "C:EURUSD",
    "GBP_USD": "C:GBPUSD",
    "USD_JPY": "C:USDJPY",
    "USD_CAD": "C:USDCAD",
    "AUD_USD": "C:AUDUSD",
    "NZD_USD": "C:NZDUSD",
    "USD_CHF": "C:USDCHF",
    "EUR_GBP": "C:EURGBP",
    "EUR_JPY": "C:EURJPY",
    "GBP_JPY": "C:GBPJPY",
}

# Polygon multiplier + timespan for each of our timeframes
TIMEFRAME_MAP = {
    "M1":  (1,  "minute"),
    "M5":  (5,  "minute"),
    "M15": (15, "minute"),
    "H1":  (1,  "hour"),
    "H4":  (4,  "hour"),
    "D":   (1,  "day"),
}

_MAX_BARS_PER_REQUEST = 50_000   # Polygon hard limit
_REQUEST_DELAY = 0.12            # stay under 5 req/s on free tier


class PolygonDownloader:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "PolygonDownloader":
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    async def fetch_candles(
        self,
        instrument: str,
        start: datetime,
        end: datetime,
        timeframe: str = "H1",
    ) -> pd.DataFrame:
        """Fetch OHLCV candles for a date range. Returns DataFrame with columns:
        time, open, high, low, close, volume.
        """
        assert self._client is not None

        ticker = PAIR_MAP.get(instrument)
        if not ticker:
            logger.error("polygon_unknown_instrument", instrument=instrument)
            return pd.DataFrame()

        multiplier, timespan = TIMEFRAME_MAP.get(timeframe, (1, "hour"))

        from_str = start.strftime("%Y-%m-%d")
        to_str   = end.strftime("%Y-%m-%d")

        url = (
            f"{BASE_URL}/{ticker}/range/{multiplier}/{timespan}"
            f"/{from_str}/{to_str}"
        )
        params = {
            "adjusted": "false",
            "sort":     "asc",
            "limit":    _MAX_BARS_PER_REQUEST,
            "apiKey":   self._api_key,
        }

        all_results = []
        next_url: Optional[str] = url

        while next_url:
            try:
                if next_url == url:
                    resp = await self._client.get(next_url, params=params)
                else:
                    resp = await self._client.get(next_url)   # next_url already has params
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.error("polygon_fetch_failed", url=next_url, error=str(exc))
                break

            status = data.get("status")
            if status not in ("OK", "DELAYED"):
                logger.warning("polygon_bad_status", status=status, instrument=instrument)
                break

            results = data.get("results") or []
            all_results.extend(results)

            logger.info(
                "polygon_page_fetched",
                instrument=instrument,
                timeframe=timeframe,
                bars=len(results),
                total=len(all_results),
            )

            next_url = data.get("next_url")
            if next_url:
                if "apiKey=" not in next_url:
                    sep = "&" if "?" in next_url else "?"
                    next_url = f"{next_url}{sep}apiKey={self._api_key}"
                await asyncio.sleep(_REQUEST_DELAY)

        if not all_results:
            logger.warning("polygon_no_data", instrument=instrument, start=from_str, end=to_str)
            return pd.DataFrame()

        df = pd.DataFrame(all_results)
        df["time"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df = df[["time", "open", "high", "low", "close", "volume"]].sort_values("time")
        df = df.reset_index(drop=True)

        logger.info(
            "polygon_fetch_complete",
            instrument=instrument,
            timeframe=timeframe,
            bars=len(df),
            from_=from_str,
            to=to_str,
        )
        return df


async def _main() -> None:
    from anchor.config import settings

    parser = argparse.ArgumentParser(description="Import Polygon.io forex history")
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--timeframe",  default="H1")
    parser.add_argument("--start",      default="2020-01-01")
    parser.add_argument("--end",        default=None)
    parser.add_argument("--output",     default=None)
    parser.add_argument("--db",         action="store_true", help="Insert into DB instead of CSV")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end   = (
        datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.end else datetime.now(timezone.utc)
    )

    api_key = settings.polygon_api_key
    if not api_key:
        print("ERROR: POLYGON_API_KEY not set in .env")
        return

    async with PolygonDownloader(api_key) as dl:
        df = await dl.fetch_candles(args.instrument, start, end, args.timeframe)

    if df.empty:
        print("No data returned.")
        return

    if args.db:
        from anchor.database.engine import init_db, get_session
        from anchor.database.models import MarketData
        from anchor.database.repositories.market_data import MarketDataRepository

        await init_db()
        rows = [
            MarketData(
                time=row.time.to_pydatetime(),
                instrument=args.instrument,
                timeframe=args.timeframe,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=int(row.volume) if row.volume else None,
                source="polygon",
            )
            for row in df.itertuples(index=False)
        ]
        _CHUNK = 500
        inserted = 0
        for i in range(0, len(rows), _CHUNK):
            chunk = rows[i:i + _CHUNK]
            async with get_session() as session:
                repo = MarketDataRepository(session)
                inserted += await repo.bulk_insert_candles(chunk)
                await session.commit()
        print(f"Inserted {inserted} rows for {args.instrument} {args.timeframe}")
    else:
        out = args.output or f"polygon_{args.instrument}_{args.timeframe}.csv"
        df.to_csv(out, index=False)
        print(f"Saved {len(df)} bars to {out}")


if __name__ == "__main__":
    asyncio.run(_main())
