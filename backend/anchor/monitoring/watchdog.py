"""
Watchdog — runs as a SEPARATE process from the engine.
Reads the system_events table for the latest HEARTBEAT.
If older than STALE_THRESHOLD_SECONDS → fires alert.

Run as: python -m anchor.monitoring.watchdog
"""
import asyncio
from datetime import timedelta

import structlog

from anchor.config import get_settings
from anchor.database.engine import init_db
from anchor.monitoring.alerts import AlertService
from anchor.utils.time_utils import utcnow

logger = structlog.get_logger(__name__)
settings = get_settings()

STALE_THRESHOLD_SECONDS = 180  # 3 minutes
CHECK_INTERVAL_SECONDS  = 120  # check every 2 minutes


class WatchdogService:
    def __init__(self, event_repo=None, alert_service: AlertService | None = None):
        self.event_repo    = event_repo
        self.alert_service = alert_service or AlertService()

    async def run(self) -> None:
        logger.info("watchdog_started")
        while True:
            try:
                await self._check()
            except Exception as exc:
                logger.error("watchdog_check_error", error=str(exc))
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def _check(self) -> None:
        if self.event_repo is None:
            return

        latest_hb = await self.event_repo.get_latest_heartbeat()

        if latest_hb is None:
            await self.alert_service.send_critical(
                "WATCHDOG: No heartbeat found in database. Engine may not have started."
            )
            return

        age = (utcnow() - latest_hb.event_at).total_seconds()
        if age > STALE_THRESHOLD_SECONDS:
            await self.alert_service.send_critical(
                f"WATCHDOG: Heartbeat is {int(age)}s old (threshold: {STALE_THRESHOLD_SECONDS}s). "
                f"Engine may be down. Last seen: {latest_hb.event_at.isoformat()}"
            )
        else:
            logger.debug("watchdog_ok", heartbeat_age_seconds=age)


async def main() -> None:
    from anchor.utils.logging import configure_logging
    configure_logging(settings.log_level)

    await init_db()

    # Import here to avoid circular import at module level
    from anchor.database.repositories.events import SystemEventRepository
    from anchor.database.engine import AsyncSessionFactory

    async with AsyncSessionFactory() as session:
        event_repo = SystemEventRepository(session)
        watchdog = WatchdogService(event_repo=event_repo)
        await watchdog.run()


if __name__ == "__main__":
    asyncio.run(main())
