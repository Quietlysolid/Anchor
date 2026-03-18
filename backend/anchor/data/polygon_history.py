"""Polygon.io historical forex data downloader.

Downloads minute aggregates for all 5 pairs from Polygon.io Currencies Starter plan
(requires 10+ year history access), then resamples to H1, H4, and D CSVs.

Usage:
    python -m anchor.data.polygon_history --api-key YOUR_KEY --output-dir data/
    # Or set POLYGON_API_KEY env var and omit --api-key
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import date, timedelta
from typing import Optional

import httpx
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

BASE_URL = "https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/minute/{from_}/{to}"

# Polygon forex tickers: C:EURUSD format
PAIR_MAP = {
    "EUR_USD": "C:EURUSD",
    "GBP_USD": "C:GBPUSD",
    "USD_JPY": "C:USDJPY",
    "AUD_USD": "C:AUDUSD",
    "USD_CAD": "C:USDCAD",
}

TIMEFRAME_FREQS = {
    "H1": "1h",
    "H4": "4h",
    "D":  "1D",
}


def _fetch_chunk(
    client: httpx.Client,
    ticker: str,
    from_: date,
    to: date,
    api_key: str,
) -> pd.DataFrame:
    """Fetch one chunk (max 50k bars) from Polygon."""
    url = BASE_URL.format(
        ticker=ticker,
        from_=from_.strftime("%Y-%m-%d"),
        to=to.strftime("%Y-%m-%d"),
    )
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000,
        "apiKey": api_key,
    }

    all_results = []
    next_url: Optional[str] = url

    while next_url:
        if next_url == url:
            resp = client.get(next_url, params=params, timeout=60.0)
        else:
            # next_url already has apiKey embedded
            resp = client.get(next_url, timeout=60.0)

        if resp.status_code == 429:
            logger.warning("polygon_rate_limit", sleeping=10)
            time.sleep(10)
            continue

        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        all_results.extend(results)

        next_url = data.get("next_url")
        if next_url:
            # Polygon next_url doesn't include apiKey
            next_url = next_url + f"&apiKey={api_key}"

    if not all_results:
        return pd.DataFrame()

    df = pd.DataFrame(all_results)
    # Polygon returns: t (epoch ms), o, h, l, c, v, vw, n
    df["time"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df[["time", "open", "high", "low", "close", "volume"]].sort_values("time")
    return df


def fetch_pair(
    pair: str,
    start: date,
    end: date,
    api_key: str,
    output_dir: str,
) -> None:
    """Download full history for one pair and save H1/H4/D CSVs."""
    ticker = PAIR_MAP[pair]
    logger.info("polygon_fetch_start", pair=pair, ticker=ticker, start=start, end=end)

    os.makedirs(output_dir, exist_ok=True)

    all_chunks: list[pd.DataFrame] = []

    # Polygon Starter allows ~50k bars per request; chunk by 6-month windows
    chunk_start = start
    with httpx.Client() as client:
        while chunk_start <= end:
            chunk_end = min(
                date(chunk_start.year + (chunk_start.month // 12),
                     (chunk_start.month % 12) + 1, 1) - timedelta(days=1)
                if chunk_start.month < 12
                else date(chunk_start.year, 12, 31),
                end,
            )
            # Simpler: just do 90-day windows
            chunk_end = min(chunk_start + timedelta(days=89), end)

            logger.info("polygon_chunk", pair=pair, from_=chunk_start, to=chunk_end)
            try:
                chunk = _fetch_chunk(client, ticker, chunk_start, chunk_end, api_key)
                if not chunk.empty:
                    all_chunks.append(chunk)
                    logger.info("polygon_chunk_ok", pair=pair, rows=len(chunk))
                else:
                    logger.warning("polygon_chunk_empty", pair=pair, from_=chunk_start)
            except Exception as exc:
                logger.error("polygon_chunk_failed", pair=pair, error=str(exc))

            chunk_start = chunk_end + timedelta(days=1)
            time.sleep(0.3)  # polite

    if not all_chunks:
        logger.error("polygon_no_data", pair=pair)
        return

    minutes = pd.concat(all_chunks, ignore_index=True).drop_duplicates("time").sort_values("time")
    minutes = minutes.set_index("time")
    logger.info("polygon_minutes_total", pair=pair, rows=len(minutes))

    # Resample and save each timeframe
    for tf, freq in TIMEFRAME_FREQS.items():
        ohlcv = minutes["close"].resample(freq).ohlc()
        ohlcv["volume"] = minutes["volume"].resample(freq).sum()
        ohlcv = ohlcv.dropna()
        ohlcv = ohlcv.reset_index()
        out_path = os.path.join(output_dir, f"{pair}_{tf}.csv")
        ohlcv.to_csv(out_path, index=False)
        logger.info("polygon_saved", pair=pair, timeframe=tf, rows=len(ohlcv), path=out_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Polygon.io forex history")
    parser.add_argument("--api-key", default=os.environ.get("POLYGON_API_KEY", ""),
                        help="Polygon API key (or set POLYGON_API_KEY env var)")
    parser.add_argument("--pairs", nargs="+",
                        default=["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD"],
                        help="Pairs to download")
    parser.add_argument("--start", default="2018-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--output-dir", default="data", help="Output directory for CSVs")
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("ERROR: Provide --api-key or set POLYGON_API_KEY env var")

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()

    for pair in args.pairs:
        if pair not in PAIR_MAP:
            logger.warning("unknown_pair", pair=pair, known=list(PAIR_MAP.keys()))
            continue
        fetch_pair(pair, start, end, args.api_key, args.output_dir)

    logger.info("polygon_all_done", pairs=args.pairs)


if __name__ == "__main__":
    main()
