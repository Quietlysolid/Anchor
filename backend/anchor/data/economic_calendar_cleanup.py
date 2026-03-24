"""Cleanup utilities for obviously invalid economic calendar rows."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, select

from anchor.database.engine import get_session, init_db
from anchor.database.models import EconomicEvent


async def _run(args) -> int:
    cutoff = datetime.now(timezone.utc) + timedelta(days=args.future_days)

    await init_db()
    async with get_session() as session:
        count_stmt = select(func.count()).where(
            and_(
                EconomicEvent.event_time >= cutoff,
                EconomicEvent.actual.isnot(None),
            )
        )
        count = (await session.execute(count_stmt)).scalar_one()
        print(
            f"Invalid future released rows: {count} "
            f"(event_time >= {cutoff.isoformat()}, actual IS NOT NULL)"
        )

        if args.dry_run or count == 0:
            return 0

        delete_stmt = delete(EconomicEvent).where(
            and_(
                EconomicEvent.event_time >= cutoff,
                EconomicEvent.actual.isnot(None),
            )
        )
        result = await session.execute(delete_stmt)
        await session.commit()
        print(f"Deleted rows: {result.rowcount or 0}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean obviously invalid economic calendar rows")
    parser.add_argument(
        "--future-days",
        type=int,
        default=1,
        help="Delete released rows strictly this many days or more into the future",
    )
    parser.add_argument("--dry-run", action="store_true")
    raise_code = asyncio.run(_run(parser.parse_args()))
    raise SystemExit(raise_code)


if __name__ == "__main__":
    main()
