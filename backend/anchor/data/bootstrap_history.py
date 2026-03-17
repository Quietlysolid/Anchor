"""Bootstrap historical candle data from Polygon.io into the database.

Downloads H1, H4, and D candles for all instruments and inserts them into
the market_data table, then optionally triggers ML retraining.

Usage (inside engine container):
    python -m anchor.data.bootstrap_history
    python -m anchor.data.bootstrap_history --start 2022-01-01
    python -m anchor.data.bootstrap_history --no-retrain
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone, timedelta

import structlog

from anchor.config import settings
from anchor.database.engine import init_db, get_session
from anchor.database.models import MarketData
from anchor.database.repositories.market_data import MarketDataRepository
from anchor.data.polygon import PolygonDownloader

logger = structlog.get_logger(__name__)

_TIMEFRAMES = ["H1", "H4", "D"]
_DEFAULT_YEARS_BACK = 3


async def _import_instrument(
    instrument: str,
    start: datetime,
    end: datetime,
    api_key: str,
) -> int:
    """Download and insert candles for one instrument across all timeframes."""
    total = 0

    async with PolygonDownloader(api_key) as dl:
        for timeframe in _TIMEFRAMES:
            logger.info("bootstrap_fetch", instrument=instrument, timeframe=timeframe,
                        from_=start.date(), to=end.date())

            candles_df = await dl.fetch_candles(instrument, start, end, timeframe)

            if candles_df.empty:
                logger.warning("bootstrap_no_data", instrument=instrument, timeframe=timeframe)
                continue

            rows = [
                MarketData(
                    time=row.time.to_pydatetime(),
                    instrument=instrument,
                    timeframe=timeframe,
                    open=float(row.open),
                    high=float(row.high),
                    low=float(row.low),
                    close=float(row.close),
                    volume=int(row.volume) if row.volume else None,
                    source="polygon",
                )
                for row in candles_df.itertuples(index=False)
            ]

            _CHUNK = 500
            inserted = 0
            for i in range(0, len(rows), _CHUNK):
                chunk = rows[i:i + _CHUNK]
                async with get_session() as session:
                    repo = MarketDataRepository(session)
                    inserted += await repo.bulk_insert_candles(chunk)
                    await session.commit()
            total += inserted
            logger.info("bootstrap_inserted", instrument=instrument,
                        timeframe=timeframe, rows=inserted)

    return total


async def main(start: datetime, end: datetime, retrain: bool) -> None:
    await init_db()
    logger.info("bootstrap_start", instruments=settings.instruments, start=start.date(), end=end.date())

    api_key = settings.polygon_api_key
    if not api_key:
        logger.error("bootstrap_no_polygon_key")
        return

    grand_total = 0
    for instrument in settings.instruments:
        rows = await _import_instrument(instrument, start, end, api_key)
        grand_total += rows
        logger.info("bootstrap_instrument_done", instrument=instrument, rows=rows)

    logger.info("bootstrap_complete", total_rows=grand_total)

    if retrain and grand_total > 0:
        logger.info("bootstrap_triggering_retraining")
        from anchor.ml.retraining import run_retraining
        results = await run_retraining()
        for instr, result in results.items():
            logger.info("retraining_result", instrument=instr, **result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bootstrap Dukascopy history into DB")
    parser.add_argument(
        "--start",
        default=(datetime.now(timezone.utc) - timedelta(days=365 * _DEFAULT_YEARS_BACK)).strftime("%Y-%m-%d"),
        help="Start date YYYY-MM-DD (default: 3 years ago)",
    )
    parser.add_argument(
        "--end",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="End date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--no-retrain",
        action="store_true",
        help="Skip ML retraining after import",
    )
    args = parser.parse_args()

    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    asyncio.run(main(start_dt, end_dt, retrain=not args.no_retrain))
