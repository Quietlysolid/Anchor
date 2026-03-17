"""
Historical CFTC COT data provider for backtesting.

Downloads CFTC Disaggregated Futures annual ZIP files (free, public) and
builds a time-indexed weekly lookup. Used by CotRedisMock to inject real
historical positioning into the backtest signal engine.

Data is cached to disk as per-year parquet files at /app/data/cot_cache/ to
avoid re-downloading on repeat backtest runs.

CFTC URL: https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip
"""
from __future__ import annotations

import io
import json
import zipfile
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# Same contract map as cot_parser.py
CONTRACT_MAP = {
    "EURO FX":          "EUR",
    "BRITISH POUND":    "GBP",
    "JAPANESE YEN":     "JPY",
    "SWISS FRANC":      "CHF",
    "AUSTRALIAN DOLLAR": "AUD",
    "NEW ZEALAND DOLLAR": "NZD",
    "CANADIAN DOLLAR":  "CAD",
}

COLUMNS_KEEP = [
    "Market_and_Exchange_Names",
    "As_of_Date_In_Form_YYMMDD",
    "Lev_Money_Positions_Long_All",
    "Lev_Money_Positions_Short_All",
]

_CACHE_DIR = Path("/app/data/cot_cache")


class HistoricalCotDatabase:
    """Multi-year CFTC COT database indexed by report date.

    Usage::

        db = HistoricalCotDatabase()
        db.load_years(2018, 2026)  # download/load from cache

        data = db.get_cot_data(some_datetime)
        # Returns {currency: {"net_noncommercial": int}} or None
    """

    def __init__(self) -> None:
        # Parallel arrays: sorted timestamps and their COT snapshots
        self._dates: list[float] = []   # Unix timestamps
        self._data: list[dict[str, int]] = []  # {currency: net_noncommercial}

    # ── Loading ──────────────────────────────────────────────────────────────

    def load_years(self, start_year: int, end_year: int) -> int:
        """Download (or load from disk cache) CFTC data for the given year range.

        Returns the total number of weekly records loaded.
        Logs a warning for any year that fails to load (network error, etc.)
        but continues with remaining years.
        """
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)

        all_records: list[tuple[datetime, dict[str, int]]] = []

        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            for year in range(start_year, end_year + 1):
                records = self._load_year(client, year)
                all_records.extend(records)
                logger.info("cot_year_loaded", year=year, records=len(records))

        # Sort ascending by date and build parallel arrays for bisect_right
        all_records.sort(key=lambda x: x[0])
        self._dates = [r[0].timestamp() for r in all_records]
        self._data  = [r[1] for r in all_records]

        logger.info("cot_historical_ready", total_records=len(self._dates))
        return len(self._dates)

    def _load_year(
        self,
        client: httpx.Client,
        year: int,
    ) -> list[tuple[datetime, dict[str, int]]]:
        cache_path = _CACHE_DIR / f"cot_{year}.parquet"

        if cache_path.exists():
            try:
                df = pd.read_parquet(cache_path)
                return self._df_to_records(df)
            except Exception as exc:
                logger.warning("cot_cache_read_failed", year=year, error=str(exc))

        url = f"https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip"
        try:
            resp = client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("cot_fetch_failed", year=year, url=url, error=str(exc))
            return []

        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                fname = next(n for n in zf.namelist() if n.endswith(".txt"))
                with zf.open(fname) as f:
                    df = pd.read_csv(f, usecols=COLUMNS_KEEP, low_memory=False)
        except Exception as exc:
            logger.warning("cot_parse_failed", year=year, error=str(exc))
            return []

        # Filter to only the contracts we care about
        pattern = "|".join(CONTRACT_MAP.keys())
        mask = df["Market_and_Exchange_Names"].str.contains(pattern, case=False, na=False)
        df = df[mask].copy()

        try:
            df.to_parquet(cache_path)
        except Exception:
            pass  # cache write failure is non-fatal

        return self._df_to_records(df)

    @staticmethod
    def _df_to_records(df: pd.DataFrame) -> list[tuple[datetime, dict[str, int]]]:
        records: list[tuple[datetime, dict[str, int]]] = []

        for date_key, group in df.groupby("As_of_Date_In_Form_YYMMDD"):
            try:
                dt = datetime.strptime(str(int(date_key)), "%y%m%d").replace(
                    tzinfo=timezone.utc
                )
            except (ValueError, TypeError):
                continue

            week_data: dict[str, int] = {}
            for _, row in group.iterrows():
                name = str(row["Market_and_Exchange_Names"]).upper()
                for contract, currency in CONTRACT_MAP.items():
                    if contract in name:
                        try:
                            net = int(row["Lev_Money_Positions_Long_All"]) - int(
                                row["Lev_Money_Positions_Short_All"]
                            )
                            week_data[currency] = net
                        except (ValueError, TypeError):
                            pass
                        break

            if week_data:
                records.append((dt, week_data))

        return records

    # ── Lookup ───────────────────────────────────────────────────────────────

    def get_cot_data(self, dt: datetime) -> Optional[dict]:
        """Return the most recent COT report on or before dt.

        Returns a dict in the same format as the Redis "cot_data" key:
            {currency: {"net_noncommercial": int}}

        Returns None when no data is available before dt (e.g. before 2018).
        """
        if not self._dates:
            return None

        ts = dt.timestamp()
        # bisect_right gives insertion point; subtract 1 for latest record <= ts
        idx = bisect_right(self._dates, ts) - 1
        if idx < 0:
            return None

        week_data = self._data[idx]
        return {
            currency: {"net_noncommercial": net}
            for currency, net in week_data.items()
        }

    @property
    def is_loaded(self) -> bool:
        return len(self._dates) > 0

    def as_redis_json(self, dt: datetime) -> Optional[str]:
        """Return get_cot_data result as a JSON string (redis.get format)."""
        data = self.get_cot_data(dt)
        return json.dumps(data) if data is not None else None
