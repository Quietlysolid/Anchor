"""Utilities for benchmark fix timing and month-end tagging."""
from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd

LONDON_TZ = ZoneInfo("Europe/London")


@dataclass(frozen=True)
class FixWindow:
    trade_date: pd.Timestamp
    fix_time_utc: pd.Timestamp
    pre_start_utc: pd.Timestamp
    pre_end_utc: pd.Timestamp
    post_end_utc: pd.Timestamp
    is_month_end: bool
    is_quarter_end: bool


@dataclass(frozen=True)
class MonthEndWindow:
    trade_date: pd.Timestamp
    anchor_month_end: pd.Timestamp
    relative_day: int
    fix_time_utc: pd.Timestamp
    pre_start_utc: pd.Timestamp
    pre_end_utc: pd.Timestamp
    post_end_utc: pd.Timestamp
    is_month_end: bool
    is_quarter_end: bool


def _is_business_day(day: pd.Timestamp) -> bool:
    return day.weekday() < 5


def _business_days(start: str, end: str) -> list[pd.Timestamp]:
    days = pd.date_range(start=start, end=end, freq="D", tz="UTC")
    return [pd.Timestamp(day).normalize() for day in days if _is_business_day(pd.Timestamp(day))]


def _last_business_day_of_month(day: pd.Timestamp) -> pd.Timestamp:
    day = pd.Timestamp(day).normalize()
    first_next_month = (day.replace(day=1) + pd.Timedelta(days=32)).replace(day=1)
    month_end = (first_next_month - pd.Timedelta(days=1)).normalize()
    cur = month_end
    while not _is_business_day(cur):
        cur -= pd.Timedelta(days=1)
    return cur.normalize()


def _is_month_end_business_day(day: pd.Timestamp) -> bool:
    return day.normalize() == _last_business_day_of_month(day)


def _is_quarter_end_business_day(day: pd.Timestamp) -> bool:
    return _is_month_end_business_day(day) and day.month in {3, 6, 9, 12}


def shift_business_day(day: pd.Timestamp, offset: int) -> pd.Timestamp:
    cur = pd.Timestamp(day).normalize()
    step = 1 if offset >= 0 else -1
    remaining = abs(offset)
    while remaining > 0:
        cur += pd.Timedelta(days=step)
        if _is_business_day(cur):
            remaining -= 1
    return cur.normalize()


def london_fix_time_utc(day: pd.Timestamp, hour: int = 16, minute: int = 0) -> pd.Timestamp:
    local_day = pd.Timestamp(day).tz_convert(LONDON_TZ) if pd.Timestamp(day).tzinfo else pd.Timestamp(day).tz_localize("UTC").tz_convert(LONDON_TZ)
    local_fix = pd.Timestamp(
        year=local_day.year,
        month=local_day.month,
        day=local_day.day,
        hour=hour,
        minute=minute,
        tz=LONDON_TZ,
    )
    return local_fix.tz_convert("UTC")


def build_fix_windows(
    start: str,
    end: str,
    pre_hours: int = 1,
    post_hours: int = 1,
) -> list[FixWindow]:
    windows: list[FixWindow] = []
    for day in _business_days(start, end):
        fix_time = london_fix_time_utc(day)
        windows.append(
            FixWindow(
                trade_date=day,
                fix_time_utc=fix_time,
                pre_start_utc=fix_time - pd.Timedelta(hours=pre_hours),
                pre_end_utc=fix_time,
                post_end_utc=fix_time + pd.Timedelta(hours=post_hours),
                is_month_end=_is_month_end_business_day(day),
                is_quarter_end=_is_quarter_end_business_day(day),
            )
        )
    return windows


def build_month_end_windows(
    start: str,
    end: str,
    offsets: tuple[int, ...] = (-1, 0, 1),
    pre_hours: int = 1,
    post_hours: int = 1,
) -> list[MonthEndWindow]:
    start_ts = pd.Timestamp(start, tz="UTC").normalize()
    end_ts = pd.Timestamp(end, tz="UTC").normalize()
    month_ends = []
    cursor = start_ts
    while cursor <= end_ts:
        me = _last_business_day_of_month(cursor)
        if start_ts <= me <= end_ts:
            month_ends.append(me)
        cursor = (cursor.replace(day=1) + pd.Timedelta(days=32)).replace(day=1)

    seen: set[tuple[pd.Timestamp, int]] = set()
    windows: list[MonthEndWindow] = []
    for month_end in month_ends:
        for offset in offsets:
            trade_day = shift_business_day(month_end, offset)
            if trade_day < start_ts or trade_day > end_ts:
                continue
            key = (trade_day, offset)
            if key in seen:
                continue
            seen.add(key)
            fix_time = london_fix_time_utc(trade_day)
            windows.append(
                MonthEndWindow(
                    trade_date=trade_day,
                    anchor_month_end=month_end,
                    relative_day=offset,
                    fix_time_utc=fix_time,
                    pre_start_utc=fix_time - pd.Timedelta(hours=pre_hours),
                    pre_end_utc=fix_time,
                    post_end_utc=fix_time + pd.Timedelta(hours=post_hours),
                    is_month_end=(offset == 0),
                    is_quarter_end=_is_quarter_end_business_day(month_end),
                )
            )
    windows.sort(key=lambda w: (w.trade_date, w.relative_day))
    return windows
