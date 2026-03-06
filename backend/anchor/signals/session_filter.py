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
# NEWYORK-only (no overlap with London) is excluded: backtest showed
# consistently negative P&L and lowest win rates across all 5 pairs.
# London and London/NY overlap retain the edge; pure NY afternoon does not.
ALLOWED_SESSIONS = {"LONDON", "OVERLAP"}


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
