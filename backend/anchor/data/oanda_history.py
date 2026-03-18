"""
Fetches historical OHLCV candles from OANDA REST API.
Used to seed the database on first run and to backfill missing data.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
import structlog

from anchor.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

TIMEFRAME_MAP = {
    "M1":  "M1",  "M5":  "M5",  "M15": "M15",
    "H1":  "H1",  "H4":  "H4",  "D":   "D",
}

# Max candles per OANDA request
MAX_CANDLES = 5000


class OANDAHistoryClient:
    def __init__(self):
        self.headers = {"Authorization": f"Bearer {settings.oanda_api_key}"}
        self.base_url = settings.oanda_base_url

    async def fetch_candles(
        self,
        instrument: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """
        Fetches all candles between start and end by paginating in batches.
        Returns DataFrame with columns: time, open, high, low, close, volume.
        """
        all_candles = []
        current = start

        async with httpx.AsyncClient(timeout=30.0) as client:
            while current < end:
                # Format as RFC3339 UTC — strip timezone info and append Z
                from_str = current.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
                url = (
                    f"{self.base_url}/v3/instruments/{instrument}/candles"
                    f"?granularity={granularity}"
                    f"&from={from_str}"
                    f"&count={MAX_CANDLES}"
                    f"&price=M"
                )
                for _attempt in range(5):
                    resp = await client.get(url, headers=self.headers)
                    if resp.status_code in (502, 503, 504):
                        await asyncio.sleep(10 * (_attempt + 1))
                        continue
                    break

                if resp.status_code == 404:
                    logger.warning("no_data", instrument=instrument, granularity=granularity)
                    break

                resp.raise_for_status()
                data = resp.json()
                candles = data.get("candles", [])

                if not candles:
                    break

                for c in candles:
                    if not c.get("complete", True):
                        continue
                    mid = c["mid"]
                    all_candles.append({
                        "time":   pd.Timestamp(c["time"]),
                        "open":   float(mid["o"]),
                        "high":   float(mid["h"]),
                        "low":    float(mid["l"]),
                        "close":  float(mid["c"]),
                        "volume": int(c.get("volume", 0)),
                    })

                last_time = pd.Timestamp(candles[-1]["time"])
                current = last_time.to_pydatetime() + timedelta(seconds=1)

                if len(candles) < MAX_CANDLES:
                    break  # no more pages

                await asyncio.sleep(0.1)  # gentle rate limiting

        if not all_candles:
            return pd.DataFrame()

        df = pd.DataFrame(all_candles)
        df.set_index("time", inplace=True)
        df.sort_index(inplace=True)
        return df


async def seed_history(
    market_data_repo,
    instruments: list[str] | None = None,
    timeframes: list[str] | None = None,
    years_back: int = 3,
) -> None:
    """Seed database with historical candles for all instruments + timeframes."""
    client = OANDAHistoryClient()
    instruments = instruments or settings.instruments
    timeframes  = timeframes  or ["H1", "H4", "D"]
    end   = datetime.utcnow()
    start = end - timedelta(days=years_back * 365)

    for instrument in instruments:
        for tf in timeframes:
            logger.info("seeding", instrument=instrument, timeframe=tf)
            df = await client.fetch_candles(instrument, tf, start, end)
            if df.empty:
                continue
            await market_data_repo.bulk_insert(instrument, tf, df)
            logger.info("seeded", instrument=instrument, timeframe=tf, rows=len(df))


async def bootstrap_from_oanda(
    start: datetime,
    end: datetime,
    retrain: bool = True,
) -> None:
    """Seed DB with OANDA history then optionally retrain ML models.

    Usage (inside engine container):
        python -m anchor.data.oanda_history
        python -m anchor.data.oanda_history --start 2024-01-01 --no-retrain
    """
    from anchor.database.engine import init_db

    await init_db()

    client = OANDAHistoryClient()
    instruments = settings.instruments
    timeframes = ["H1", "H4", "D"]

    grand_total = 0

    for instrument in instruments:
        for tf in timeframes:
            logger.info("oanda_bootstrap_start", instrument=instrument, timeframe=tf,
                        start=start.date(), end=end.date())
            df = await client.fetch_candles(instrument, tf, start, end)
            logger.info("oanda_fetched", instrument=instrument, df_len=len(df))
            if df.empty:
                logger.warning("oanda_no_data", instrument=instrument, timeframe=tf)
                continue

            def _to_naive_utc(ts):
                dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt

            import csv
            import io
            import asyncpg

            buf = io.StringIO()
            writer = csv.writer(buf)
            row_count = 0
            for ts, row in df.iterrows():
                t = _to_naive_utc(ts)
                writer.writerow([
                    t.strftime("%Y-%m-%d %H:%M:%S"),
                    instrument, tf,
                    float(row.open), float(row.high), float(row.low), float(row.close),
                    int(row.volume) if row.volume else "",
                    "",
                    "oanda",
                ])
                row_count += 1

            db_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
            conn = await asyncpg.connect(db_url)
            try:
                # Use a temp table + INSERT ON CONFLICT DO NOTHING so re-runs are idempotent.
                # All statements run inside one explicit transaction so ON COMMIT DROP works.
                async with conn.transaction():
                    await conn.execute("""
                        CREATE TEMP TABLE _md_stage (
                            time        TIMESTAMPTZ,
                            instrument  TEXT,
                            timeframe   TEXT,
                            open        NUMERIC,
                            high        NUMERIC,
                            low         NUMERIC,
                            close       NUMERIC,
                            volume      INTEGER,
                            spread_avg  NUMERIC,
                            source      TEXT
                        ) ON COMMIT DROP
                    """)
                    await conn.copy_to_table(
                        "_md_stage",
                        source=io.BytesIO(buf.getvalue().encode()),
                        columns=["time", "instrument", "timeframe", "open", "high", "low", "close", "volume", "spread_avg", "source"],
                        format="csv",
                    )
                    result = await conn.execute("""
                        INSERT INTO market_data
                            (time, instrument, timeframe, open, high, low, close, volume, spread_avg, source)
                        SELECT time, instrument, timeframe, open, high, low, close, volume, spread_avg, source
                        FROM _md_stage
                        ON CONFLICT (time, instrument, timeframe) DO NOTHING
                    """)
                    inserted = int(result.split()[-1]) if result else 0
            finally:
                await conn.close()

            grand_total += row_count
            logger.info("oanda_inserted", instrument=instrument, timeframe=tf,
                        rows=row_count, inserted=inserted)

    logger.info("oanda_bootstrap_complete", total_rows=grand_total)

    if retrain and grand_total > 0:
        logger.info("bootstrap_triggering_retraining")
        from anchor.ml.retraining import run_retraining
        results = await run_retraining()
        for instr, result in results.items():
            logger.info("retraining_result", instrument=instr, **result)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Bootstrap OANDA history into DB")
    parser.add_argument(
        "--start",
        default=(datetime.now(timezone.utc) - timedelta(days=365 * 2)).strftime("%Y-%m-%d"),
        help="Start date YYYY-MM-DD (default: 2 years ago)",
    )
    parser.add_argument(
        "--end",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="End date YYYY-MM-DD (default: today)",
    )
    parser.add_argument("--no-retrain", action="store_true", help="Skip ML retraining")
    args = parser.parse_args()

    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt   = datetime.strptime(args.end,   "%Y-%m-%d").replace(tzinfo=timezone.utc)

    asyncio.run(bootstrap_from_oanda(start_dt, end_dt, retrain=not args.no_retrain))
