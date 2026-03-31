from __future__ import annotations

from typing import List, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.database.models import EventSeverity, SystemEvent


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
