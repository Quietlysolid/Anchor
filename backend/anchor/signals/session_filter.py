"""
Session and day-of-week filter.
Suppresses signals during low-quality trading periods.
"""
from datetime import datetime

from anchor.config import get_settings as _get_settings
from anchor.utils.time_utils import (
    is_monday_open_risk,
    is_friday_thin_liquidity,
    is_weekend_close_window,
    is_london_open_noise,
    get_session_name,
)


# Sessions where signal generation is allowed for most pairs.
# OOS analysis (2024-2026) on EUR_USD shows:
#   LONDON (07:15-12 UTC): 56% WR, PF 1.29 at 1:1 R:R  ← profitable
#   OVERLAP (12-17 UTC):   44% WR, PF 0.80 at 1:1 R:R  ← consistently losing
#   NEWYORK (17-21 UTC):   excluded (worst performance)
# Restricting to LONDON-only improves PF from 0.95 to 1.29.
ALLOWED_SESSIONS = {"LONDON"}

# JPY pairs trade well in Asian session (00:00-09:00 UTC) — Tokyo is their
# primary market. OOS data on USD_JPY shows Asian session has comparable
# WR to London (~48%) with lower spread and cleaner ranging structure.
# Derived from active instruments in config so this stays in sync automatically.
JPY_INSTRUMENTS = {i for i in _get_settings().instruments if "JPY" in i}
ASIAN_SESSION_START = 0   # UTC hour
ASIAN_SESSION_END   = 3   # UTC hour — use 00:00-03:00, the cleanest Tokyo window


def check_session(dt: datetime, instrument: str = "") -> tuple[bool, str]:
    """
    Returns (allowed: bool, reason: str).
    Suppresses: Monday open gap risk, Friday thin liquidity, weekends.

    instrument: optional — when provided, JPY pairs are additionally allowed
                during the Asian session (00:00-03:00 UTC).
    """
    if is_weekend_close_window(dt):
        return False, "WEEKEND_CLOSE"

    if is_monday_open_risk(dt):
        return False, "MONDAY_GAP_RISK"

    if is_friday_thin_liquidity(dt):
        return False, "FRIDAY_THIN_LIQUIDITY"

    session = get_session_name(dt)

    # JPY pairs: allow Asian session (00:00-03:00 UTC, Tokyo core hours)
    if instrument in JPY_INSTRUMENTS:
        hour = dt.hour
        if ASIAN_SESSION_START <= hour < ASIAN_SESSION_END:
            return True, "ASIAN"

    if is_london_open_noise(dt):
        return False, "LONDON_OPEN_NOISE"

    if session not in ALLOWED_SESSIONS:
        return False, f"OFF_SESSION_{session}"

    return True, session
