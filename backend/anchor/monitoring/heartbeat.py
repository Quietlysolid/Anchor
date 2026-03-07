"""
Heartbeat service. Writes a HEARTBEAT event to the database every 60 seconds.
The watchdog (separate process) reads this and alerts if it goes stale.
"""
import asyncio

import structlog

from anchor.database.models import SystemEvent, EventSeverity
from anchor.utils.time_utils import utcnow

logger = structlog.get_logger(__name__)

HEARTBEAT_INTERVAL = 60  # seconds


class HeartbeatService:
    def __init__(self):
        self._running = False

    async def run(self) -> None:
        # Import lazily so AsyncSessionFactory is already initialised by init_db()
        from anchor.database.engine import AsyncSessionFactory
        from anchor.api.websocket import manager as ws_manager

        self._running = True
        while self._running:
            now = utcnow()
            try:
                if AsyncSessionFactory is not None:
                    async with AsyncSessionFactory() as session:
                        async with session.begin():
                            session.add(SystemEvent(
                                event_type="HEARTBEAT",
                                severity=EventSeverity.INFO,
                                component="ENGINE",
                                message="alive",
                                metadata_={"ts": now.isoformat()},
                            ))
                    logger.debug("heartbeat_written")
                else:
                    logger.warning("heartbeat_skipped_no_db")
            except Exception as exc:
                logger.error("heartbeat_error", error=str(exc))

            # Broadcast over WebSocket so the dashboard updates immediately
            try:
                await ws_manager.broadcast("heartbeat", {"ts": now.isoformat()})
            except Exception as exc:
                logger.warning("heartbeat_ws_error", error=str(exc))

            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def stop(self) -> None:
        self._running = False
