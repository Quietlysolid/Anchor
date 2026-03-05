"""
News / economic calendar filter.
Suppresses signals within 2 hours of HIGH-impact events for affected currencies.
"""
from datetime import datetime, timedelta

import structlog

logger = structlog.get_logger(__name__)

# Which currencies are involved in each instrument
INSTRUMENT_CURRENCIES: dict[str, list[str]] = {
    "EUR_USD": ["EUR", "USD"], "GBP_USD": ["GBP", "USD"],
    "USD_JPY": ["USD", "JPY"], "USD_CHF": ["USD", "CHF"],
    "AUD_USD": ["AUD", "USD"], "NZD_USD": ["NZD", "USD"],
    "USD_CAD": ["USD", "CAD"], "EUR_JPY": ["EUR", "JPY"],
    "GBP_JPY": ["GBP", "JPY"], "AUD_JPY": ["AUD", "JPY"],
}

NEWS_SUPPRESSION_WINDOW_HOURS = 2


class NewsFilter:
    def __init__(self, calendar_repo=None):
        self.calendar_repo = calendar_repo

    async def check(
        self,
        instrument: str,
        dt: datetime,
    ) -> tuple[bool, str | None]:
        """
        Returns (allowed: bool, reason: str | None).
        """
        if self.calendar_repo is None:
            return True, None  # no calendar data, allow

        currencies = INSTRUMENT_CURRENCIES.get(instrument, [])
        if not currencies:
            return True, None

        window_start = dt - timedelta(hours=NEWS_SUPPRESSION_WINDOW_HOURS)
        window_end   = dt + timedelta(hours=NEWS_SUPPRESSION_WINDOW_HOURS)

        events = await self.calendar_repo.get_high_impact_events(
            currencies=currencies,
            start=window_start,
            end=window_end,
        )

        if events:
            event_names = ", ".join(e.event_name for e in events[:3])
            return False, f"NEWS:{event_names}"

        return True, None
