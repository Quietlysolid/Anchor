from datetime import datetime, time
import pytz

UTC = pytz.UTC


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def is_london_session(dt: datetime) -> bool:
    """07:00–17:00 UTC"""
    t = dt.time()
    return time(7, 0) <= t < time(17, 0)


def is_newyork_session(dt: datetime) -> bool:
    """12:00–21:00 UTC"""
    t = dt.time()
    return time(12, 0) <= t < time(21, 0)


def is_asian_session(dt: datetime) -> bool:
    """00:00–09:00 UTC"""
    t = dt.time()
    return time(0, 0) <= t < time(9, 0)


def is_london_newyork_overlap(dt: datetime) -> bool:
    """12:00–17:00 UTC — highest liquidity"""
    t = dt.time()
    return time(12, 0) <= t < time(17, 0)


def get_session_name(dt: datetime) -> str:
    if is_london_newyork_overlap(dt):
        return "OVERLAP"
    if is_london_session(dt):
        return "LONDON"
    if is_newyork_session(dt):
        return "NEWYORK"
    if is_asian_session(dt):
        return "ASIAN"
    return "OFF"


def is_london_open_noise(dt: datetime) -> bool:
    """07:00–07:15 UTC — first 15 minutes of London open.

    Price action in this window is dominated by stale Asian orders being
    hit, market makers testing liquidity, and algorithmic stop hunts.
    OOS analysis shows ~35% WR in this window vs ~52% WR for 07:15-12:00.
    Skipping it costs ~5% of London signals but removes the worst-WR entries.
    """
    t = dt.time()
    return time(7, 0) <= t < time(7, 15)


def is_weekend_close_window(dt: datetime) -> bool:
    """Friday after 20:30 UTC — close all positions."""
    return dt.weekday() == 4 and dt.time() >= time(20, 30)


def is_monday_open_risk(dt: datetime) -> bool:
    """Monday before 02:00 UTC — gap risk from weekend."""
    return dt.weekday() == 0 and dt.time() < time(2, 0)


def is_friday_thin_liquidity(dt: datetime) -> bool:
    """Friday after 18:00 UTC — thin liquidity."""
    return dt.weekday() == 4 and dt.time() >= time(18, 0)
