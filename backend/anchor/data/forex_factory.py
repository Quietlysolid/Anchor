"""ForexFactory economic calendar scraper.

Scrapes the weekly calendar and stores HIGH-impact events.
Respects robots.txt — minimum 5-second delay between requests.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

import httpx
import structlog
from bs4 import BeautifulSoup

from anchor.database.models import EconomicEvent

logger = structlog.get_logger(__name__)

FF_URL = "https://www.forexfactory.com/calendar"
FF_TIMEZONE = ZoneInfo("America/New_York")  # FF uses US Eastern

IMPACT_MAP = {
    "icon--ff-impact-red": "HIGH",
    "icon--ff-impact-ora": "MEDIUM",
    "icon--ff-impact-yel": "LOW",
    "icon--ff-impact-gra": "HOLIDAY",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AnchorBot/1.0; +https://github.com/)",
    "Accept-Language": "en-US,en;q=0.9",
}


class ForexFactoryScraper:
    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "ForexFactoryScraper":
        self._client = httpx.AsyncClient(headers=HEADERS, timeout=30.0, follow_redirects=True)
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    async def fetch_week(self, for_date: Optional[date] = None) -> List[EconomicEvent]:
        """Fetch all calendar events for the week containing `for_date`."""
        assert self._client is not None
        params = {}
        if for_date:
            params["week"] = for_date.strftime("%b%d.%Y").lower()

        try:
            resp = await self._client.get(FF_URL, params=params)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("forex_factory_fetch_failed", error=str(exc))
            return []

        await asyncio.sleep(5)  # polite delay
        return self._parse(resp.text, anchor_date=for_date)

    def _parse(self, html: str, anchor_date: Optional[date] = None) -> List[EconomicEvent]:
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table", class_="calendar__table")
        if not table:
            logger.warning("forex_factory_no_table_found")
            return []

        events: List[EconomicEvent] = []
        current_date: Optional[date] = None

        for row in table.find_all("tr", class_="calendar__row"):
            # Date cell (only present on first row of a new date)
            date_cell = row.find("td", class_="calendar__date")
            if date_cell and date_cell.get_text(strip=True):
                try:
                    # FF format: "Mon Jan 6" or "MonJan 6" (no space between day/month)
                    import re as _re
                    text = date_cell.get_text(strip=True)
                    # Insert space between weekday abbrev and month if missing
                    text = _re.sub(r'^([A-Za-z]{3})([A-Za-z])', r'\1 \2', text)
                    base_year = anchor_date.year if anchor_date else datetime.now().year
                    parsed = datetime.strptime(f"{text} {base_year}", "%a %b %d %Y").date()
                    if anchor_date:
                        if anchor_date.month == 12 and parsed.month == 1:
                            parsed = parsed.replace(year=base_year + 1)
                        elif anchor_date.month == 1 and parsed.month == 12:
                            parsed = parsed.replace(year=base_year - 1)
                    current_date = parsed
                except ValueError:
                    pass

            if current_date is None:
                continue

            # Impact
            impact_cell = row.find("td", class_="calendar__impact")
            if not impact_cell:
                continue
            impact = "UNKNOWN"
            for css_class, label in IMPACT_MAP.items():
                if impact_cell.find(class_=css_class):
                    impact = label
                    break

            if impact not in ("HIGH", "MEDIUM"):
                continue

            # Time
            time_cell = row.find("td", class_="calendar__time")
            time_text = time_cell.get_text(strip=True) if time_cell else ""
            try:
                if time_text and time_text != "All Day":
                    naive_dt = datetime.strptime(f"{current_date} {time_text}", "%Y-%m-%d %I:%M%p")
                    event_time = naive_dt.replace(tzinfo=FF_TIMEZONE).astimezone(timezone.utc)
                else:
                    event_time = datetime.combine(current_date, datetime.min.time()).replace(
                        tzinfo=timezone.utc
                    )
            except ValueError:
                continue

            # Currency
            currency_cell = row.find("td", class_="calendar__currency")
            currency = currency_cell.get_text(strip=True) if currency_cell else ""
            if not currency:
                continue

            # Title
            title_cell = row.find("td", class_="calendar__event")
            title = title_cell.get_text(strip=True) if title_cell else "Unknown"

            # Consensus / released values (may be empty for future events)
            forecast_cell  = row.find("td", class_="calendar__forecast")
            previous_cell  = row.find("td", class_="calendar__previous")
            actual_cell    = row.find("td", class_="calendar__actual")
            forecast_val   = forecast_cell.get_text(strip=True)  if forecast_cell  else None
            previous_val   = previous_cell.get_text(strip=True)  if previous_cell  else None
            actual_val     = actual_cell.get_text(strip=True)     if actual_cell    else None

            events.append(
                EconomicEvent(
                    event_time=event_time,
                    currency=currency,
                    impact=impact,
                    event_name=title,
                    forecast=forecast_val  or None,
                    previous=previous_val  or None,
                    actual=actual_val      or None,
                )
            )

        logger.info("forex_factory_parsed", count=len(events))
        return events
