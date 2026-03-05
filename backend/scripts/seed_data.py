"""
One-time data seed script.
Run inside the engine container:
  docker compose exec engine python scripts/seed_data.py

Seeds:
  1. OANDA historical candles (H1, H4, D) — last 3 years for all 5 instruments
  2. ForexFactory economic calendar — current + next week
"""
import asyncio
import sys
from datetime import datetime, timedelta, date

import structlog

logger = structlog.get_logger(__name__)


async def seed_candles():
    from anchor.config import get_settings
    from anchor.database.engine import AsyncSessionFactory
    from anchor.data.oanda_history import OANDAHistoryClient

    settings = get_settings()

    client = OANDAHistoryClient()
    from datetime import timezone
    end = datetime.now(timezone.utc)

    # M15: 90 days; H1/H4/D: 3 years
    tf_config = {
        "M15": timedelta(days=90),
        "H1":  timedelta(days=3 * 365),
        "H4":  timedelta(days=3 * 365),
        "D":   timedelta(days=3 * 365),
    }
    timeframes = list(tf_config.keys())

    total = 0
    for instrument in settings.instruments:
        for tf in timeframes:
            start = end - tf_config[tf]
            logger.info("fetching", instrument=instrument, timeframe=tf)
            try:
                df = await client.fetch_candles(instrument, tf, start, end)
            except Exception as exc:
                logger.error("fetch_failed", instrument=instrument, timeframe=tf, error=str(exc))
                continue

            if df.empty:
                logger.warning("no_data", instrument=instrument, timeframe=tf)
                continue

            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from anchor.database.models import MarketData as _MD

            rows = [
                {
                    "instrument": instrument,
                    "timeframe": tf,
                    "time": row.Index.to_pydatetime(),
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": int(row.volume),
                    "source": "oanda",
                }
                for row in df.itertuples()
            ]

            # Insert in chunks of 500 to stay under asyncpg's 32767 param limit
            CHUNK = 500
            inserted = 0
            for i in range(0, len(rows), CHUNK):
                chunk = rows[i:i + CHUNK]
                async with AsyncSessionFactory() as session:
                    async with session.begin():
                        stmt = pg_insert(_MD).values(chunk).on_conflict_do_nothing()
                        result = await session.execute(stmt)
                        inserted += result.rowcount

            total += inserted
            logger.info("seeded", instrument=instrument, timeframe=tf, rows=inserted)

    logger.info("candle_seed_complete", total=total)


async def seed_calendar():
    from anchor.database.engine import AsyncSessionFactory
    from anchor.data.forex_factory import ForexFactoryScraper
    from anchor.database.repositories import EconomicCalendarRepository
    from datetime import date

    today = date.today()
    next_week = today + timedelta(days=7)

    async with ForexFactoryScraper() as scraper:
        logger.info("fetching_calendar_this_week")
        events = await scraper.fetch_week(today)
        logger.info("fetching_calendar_next_week")
        events += await scraper.fetch_week(next_week)

    if not events:
        logger.warning("no_calendar_events_fetched")
        return

    async with AsyncSessionFactory() as session:
        async with session.begin():
            repo = EconomicCalendarRepository(session)
            count = await repo.insert_many(events)

    logger.info("calendar_seed_complete", events=count)


async def main():
    from anchor.database.engine import init_db
    await init_db()

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode in ("all", "candles"):
        await seed_candles()

    if mode in ("all", "calendar"):
        await seed_calendar()


if __name__ == "__main__":
    asyncio.run(main())
