"""
News / economic calendar filter.

Returns a 3-tuple: (allowed, reason, size_multiplier)

Suppression tiers:
  HIGH-impact  — full suppress within ±2h of event
  MEDIUM-impact — half-size within ±1h of event (trade allowed, size halved)
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

HIGH_PRE_WINDOW_HOURS    = 2     # suppress this many hours BEFORE a HIGH event
HIGH_POST_WINDOW_MINUTES = 45    # suppress this many minutes AFTER a HIGH event releases
MEDIUM_WINDOW_HOURS      = 1
MEDIUM_SIZE_SCALE        = 0.5

# Kept for backwards compat (used in tests/backtest)
HIGH_WINDOW_HOURS = HIGH_PRE_WINDOW_HOURS


class NewsFilter:
    def __init__(self, calendar_repo=None):
        self.calendar_repo = calendar_repo

    async def check(
        self,
        instrument: str,
        dt: datetime,
    ) -> tuple[bool, str | None, float]:
        """
        Returns (allowed, reason, size_multiplier).

        size_multiplier is 1.0 normally, 0.5 near MEDIUM events.
        allowed=False means suppress entirely (HIGH events).
        """
        if self.calendar_repo is None:
            return True, None, 1.0

        currencies = INSTRUMENT_CURRENCIES.get(instrument, [])
        if not currencies:
            return True, None, 1.0

        # ── HIGH-impact: asymmetric pre/post window ────────────────────
        # Pre-event  (event in the future):  suppress full 2h  — unknown outcome
        # Post-event (event already released): suppress 45min only — let
        #   continuation trades through once the initial spike has settled.
        #   If actual is None the event hasn't printed yet; treat as pre-event.
        high_start = dt - timedelta(minutes=HIGH_POST_WINDOW_MINUTES)
        high_end   = dt + timedelta(hours=HIGH_PRE_WINDOW_HOURS)

        high_events = await self.calendar_repo.get_affecting_currencies(
            currencies=currencies,
            start=high_start,
            end=high_end,
            impact="HIGH",
        )
        if high_events:
            # Split into pre-event (future or unreleased) vs post-event (released, within 45min)
            pre_events  = [e for e in high_events
                           if e.event_time > dt or getattr(e, "actual", None) is None]
            post_events = [e for e in high_events
                           if e.event_time <= dt
                           and getattr(e, "actual", None) is not None
                           and e.event_time > dt - timedelta(minutes=HIGH_POST_WINDOW_MINUTES)]

            if pre_events:
                event_names = ", ".join(e.event_name for e in pre_events[:3])
                return False, f"NEWS_PRE:{event_names}", 1.0

            if post_events:
                event_names = ", ".join(e.event_name for e in post_events[:3])
                return False, f"NEWS_POST_NOISE:{event_names}", 1.0

        # ── MEDIUM-impact: half-size ±1h ──────────────────────────────────
        med_start = dt - timedelta(hours=MEDIUM_WINDOW_HOURS)
        med_end   = dt + timedelta(hours=MEDIUM_WINDOW_HOURS)

        med_events = await self.calendar_repo.get_affecting_currencies(
            currencies=currencies,
            start=med_start,
            end=med_end,
            impact="MEDIUM",
        )
        if med_events:
            event_names = ", ".join(e.event_name for e in med_events[:3])
            logger.debug(
                "news_medium_size_reduced",
                instrument=instrument,
                events=event_names,
                size_scale=MEDIUM_SIZE_SCALE,
            )
            return True, None, MEDIUM_SIZE_SCALE

        return True, None, 1.0
