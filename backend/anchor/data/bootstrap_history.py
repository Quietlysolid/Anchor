"""Bootstrap historical candle data from Dukascopy into the database.

Downloads 3 years of H1 candles for all instruments and inserts them into
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
from anchor.database.engine import get_session
from anchor.database.models import MarketData
from anchor.database.repositories.market_data import MarketDataRepository
from anchor.data.dukascopy import DukascopyDownloader

logger = structlog.get_logger(__name__)

_TIMEFRAME = "H1"
_CHUNK_DAYS = 30          # insert in monthly chunks to keep memory low
_DEFAULT_YEARS_BACK = 3


async def _import_instrument(
    instrument: str,
    start: datetime,
    end: datetime,
) -> int:
    """Download and insert candles for one instrument. Returns rows inserted."""
    total = 0
    current = start

    async with DukascopyDownloader() as dl:
        while current < end:
            chunk_end = min(current + timedelta(days=_CHUNK_DAYS), end)
            logger.info(
                "bootstrap_chunk",
                instrument=instrument,
                from_=current.date(),
                to=chunk_end.date(),
            )

            candles_df = await dl.fetch_range_as_candles(
                instrument, current, chunk_end, _TIMEFRAME
            )

            if not candles_df.empty:
                rows = [
                    MarketData(
                        time=row.time.to_pydatetime() if hasattr(row.time, "to_pydatetime") else row.time,
                        instrument=instrument,
                        timeframe=_TIMEFRAME,
                        open=float(row.open),
                        high=float(row.high),
                        low=float(row.low),
                        close=float(row.close),
                        volume=int(row.volume) if row.volume else None,
                        spread_avg=float(row.spread) if hasattr(row, "spread") and row.spread else None,
                        source="dukascopy",
                    )
                    for row in candles_df.itertuples(index=False)
                ]

                async with get_session() as session:
                    repo = MarketDataRepository(session)
                    # Skip rows that already exist (re-run safe)
                    existing = await repo.get_candles(
                        instrument, _TIMEFRAME, current, chunk_end, limit=50000
                    )
                    existing_times = {r.time for r in existing}
                    new_rows = [r for r in rows if r.time not in existing_times]
                    if new_rows:
                        await repo.bulk_insert_candles(new_rows)
                        await session.commit()
                        total += len(new_rows)
                        logger.info("bootstrap_inserted", instrument=instrument, rows=len(new_rows))

            current = chunk_end + timedelta(seconds=1)

    return total


async def main(start: datetime, end: datetime, retrain: bool) -> None:
    logger.info("bootstrap_start", instruments=settings.instruments, start=start.date(), end=end.date())

    grand_total = 0
    for instrument in settings.instruments:
        rows = await _import_instrument(instrument, start, end)
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
