"""
Session and day-of-week filter.
Suppresses signals during low-quality trading periods.
"""
from datetime import datetime

from anchor.utils.time_utils import (
    is_monday_open_risk,
    is_friday_thin_liquidity,
    is_weekend_close_window,
    get_session_name,
)


# Sessions where signal generation is allowed.
# OOS analysis (2024-2026) on EUR_USD shows:
#   LONDON (07-12 UTC):  56% WR, PF 1.29 at 1:1 R:R  ← profitable
#   OVERLAP (12-17 UTC): 44% WR, PF 0.80 at 1:1 R:R  ← consistently losing
#   NEWYORK (17-21 UTC): excluded (worst performance)
# Restricting to LONDON-only improves PF from 0.95 to 1.29.
ALLOWED_SESSIONS = {"LONDON"}


def check_session(dt: datetime) -> tuple[bool, str]:
    """
    Returns (allowed: bool, reason: str).
    Suppresses: Monday open gap risk, Friday thin liquidity, weekends.
    """
    if is_weekend_close_window(dt):
        return False, "WEEKEND_CLOSE"

    if is_monday_open_risk(dt):
        return False, "MONDAY_GAP_RISK"

    if is_friday_thin_liquidity(dt):
        return False, "FRIDAY_THIN_LIQUIDITY"

    session = get_session_name(dt)
    if session not in ALLOWED_SESSIONS:
        return False, f"OFF_SESSION_{session}"

    return True, session
