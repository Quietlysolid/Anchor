"""
Fetches historical OHLCV candles from OANDA REST API.
Used to seed the database on first run and to backfill missing data.
"""
import asyncio
from datetime import datetime, timedelta

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
                resp = await client.get(url, headers=self.headers)

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


if __name__ == "__main__":
    asyncio.run(seed_history(None))  # wire up repo when running standalone
