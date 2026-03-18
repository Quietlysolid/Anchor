"""Holiday calendar — suppress trading on major market holidays.

Major bank holidays cause thin liquidity and erratic price action.
We suppress all signals on these days.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Set

# Hard-coded list of known low-liquidity days (extend as needed)
# Format: (month, day) for recurring annual holidays
RECURRING_HOLIDAYS: Set[tuple] = {
    (1, 1),   # New Year's Day
    (12, 25), # Christmas Day
    (12, 26), # Boxing Day (UK/AUS/NZD)
}

# One-off dates (YYYY, MM, DD)
ONE_OFF_HOLIDAYS: Set[tuple] = set()


def is_holiday(dt: datetime | None = None) -> bool:
    """Return True if the given datetime falls on a major market holiday."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    d = dt.date() if isinstance(dt, datetime) else dt
    if (d.month, d.day) in RECURRING_HOLIDAYS:
        return True
    if (d.year, d.month, d.day) in ONE_OFF_HOLIDAYS:
        return True
    return False


def add_one_off(year: int, month: int, day: int) -> None:
    ONE_OFF_HOLIDAYS.add((year, month, day))
