from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, and_, desc, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.database.models import SystemEvent, EconomicEvent, EventSeverity


class SystemEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert(
        self,
        event: SystemEvent | None = None,
        *,
        event_type: str | None = None,
        severity: str = "INFO",
        component: str = "SYSTEM",
        message: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Accept either a pre-built SystemEvent or keyword args."""
        if event is None:
            event = SystemEvent(
                event_type=event_type,
                severity=severity,
                component=component,
                message=message,
                metadata_=metadata,
            )
        self.session.add(event)
        await self.session.flush()

    async def get_latest_heartbeat(self) -> Optional[SystemEvent]:
        result = await self.session.execute(
            select(SystemEvent)
            .where(SystemEvent.event_type == "HEARTBEAT")
            .order_by(desc(SystemEvent.event_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_recent(self, limit: int = 100) -> List[SystemEvent]:
        result = await self.session.execute(
            select(SystemEvent)
            .order_by(desc(SystemEvent.event_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_errors(self, limit: int = 50) -> List[SystemEvent]:
        result = await self.session.execute(
            select(SystemEvent)
            .where(SystemEvent.severity == EventSeverity.ERROR)
            .order_by(desc(SystemEvent.event_at))
            .limit(limit)
        )
        return list(result.scalars().all())


class EconomicCalendarRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert_many(self, events: List[EconomicEvent]) -> int:
        """Insert events, skipping duplicates on (event_time, currency, event_name)."""
        if not events:
            return 0

        keys = [(e.event_time, e.currency, e.event_name) for e in events]
        existing = await self.session.execute(
            select(EconomicEvent.event_time, EconomicEvent.currency, EconomicEvent.event_name)
            .where(
                tuple_(
                    EconomicEvent.event_time,
                    EconomicEvent.currency,
                    EconomicEvent.event_name,
                ).in_(keys)
            )
        )
        existing_keys = {(r.event_time, r.currency, r.event_name) for r in existing}

        new_events = [
            e for e in events
            if (e.event_time, e.currency, e.event_name) not in existing_keys
        ]
        if new_events:
            self.session.add_all(new_events)
            await self.session.flush()
        return len(new_events)

    async def get_upcoming(
        self, start: datetime, end: datetime, impact: Optional[str] = None
    ) -> List[EconomicEvent]:
        filters = [
            EconomicEvent.event_time >= start,
            EconomicEvent.event_time <= end,
        ]
        if impact:
            filters.append(EconomicEvent.impact == impact)
        result = await self.session.execute(
            select(EconomicEvent)
            .where(and_(*filters))
            .order_by(EconomicEvent.event_time)
        )
        return list(result.scalars().all())

    async def get_affecting_currencies(
        self,
        currencies: List[str],
        start: datetime,
        end: datetime,
        impact: str = "HIGH",
    ) -> List[EconomicEvent]:
        result = await self.session.execute(
            select(EconomicEvent)
            .where(
                and_(
                    EconomicEvent.currency.in_(currencies),
                    EconomicEvent.impact == impact,
                    EconomicEvent.event_time >= start,
                    EconomicEvent.event_time <= end,
                )
            )
            .order_by(EconomicEvent.event_time)
        )
        return list(result.scalars().all())

    async def get_recent_releases(
        self, currencies: List[str], limit: int = 6
    ) -> List[EconomicEvent]:
        """Return the most recent HIGH-impact events where actual was recorded."""
        result = await self.session.execute(
            select(EconomicEvent)
            .where(
                and_(
                    EconomicEvent.currency.in_(currencies),
                    EconomicEvent.impact == "HIGH",
                    EconomicEvent.actual.isnot(None),
                )
            )
            .order_by(desc(EconomicEvent.event_time))
            .limit(limit)
        )
        return list(result.scalars().all())
