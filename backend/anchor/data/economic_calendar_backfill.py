"""Historical ForexFactory calendar backfill.

Fetches one week at a time and upserts into `economic_calendar`, allowing
historical event studies to use actual/forecast release values instead of only
the forward-looking weekly snapshot maintained by the scheduler.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta

import pandas as pd

from anchor.data.forex_factory import ForexFactoryScraper
from anchor.database.engine import get_session, init_db
from anchor.database.repositories import EconomicCalendarRepository


def _monday(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value, tz="UTC").normalize()
    return ts - pd.Timedelta(days=ts.weekday())


async def _run(args) -> int:
    start = _monday(args.start)
    end = _monday(args.end)
    weeks = pd.date_range(start=start, end=end, freq="W-MON", tz="UTC")
    if len(weeks) == 0:
        print("No weeks selected.")
        return 0

    await init_db()

    total_changed = 0
    async with ForexFactoryScraper() as scraper:
        for week in weeks:
            async with get_session() as session:
                repo = EconomicCalendarRepository(session)
                events = await scraper.fetch_week(week.date())
                changed = await repo.insert_many(events)
                await session.commit()
                total_changed += changed
                print(
                    f"{week.date().isoformat()}  fetched={len(events):>3}  changed={changed:>3}"
                )
            if args.pause_seconds > 0:
                await asyncio.sleep(args.pause_seconds)

    print(
        f"\nBackfill complete: weeks={len(weeks)}  total_changed={total_changed}  "
        f"range={start.date().isoformat()}..{(end + timedelta(days=6)).date().isoformat()}"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical economic calendar weeks")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=pd.Timestamp.utcnow().strftime("%Y-%m-%d"))
    parser.add_argument("--pause-seconds", type=float, default=0.0)
    raise_code = asyncio.run(_run(parser.parse_args()))
    raise SystemExit(raise_code)


if __name__ == "__main__":
    main()
