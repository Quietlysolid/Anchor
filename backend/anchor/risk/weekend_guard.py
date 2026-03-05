"""
Weekend gap protection.
Closes all positions by Friday 20:30 UTC.
"""
import asyncio
from datetime import datetime

import structlog

from anchor.utils.time_utils import utcnow

logger = structlog.get_logger(__name__)

FRIDAY   = 4
CLOSE_HOUR   = 20
CLOSE_MINUTE = 30


class WeekendGuard:
    def __init__(self, broker_client=None, position_repo=None):
        self.broker_client = broker_client
        self.position_repo = position_repo
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            now = utcnow()
            if self._is_close_time(now):
                await self._close_all_positions()
            await asyncio.sleep(60)  # check every minute

    async def stop(self) -> None:
        self._running = False

    def _is_close_time(self, dt: datetime) -> bool:
        return (
            dt.weekday() == FRIDAY
            and dt.hour == CLOSE_HOUR
            and dt.minute >= CLOSE_MINUTE
        )

    async def _close_all_positions(self) -> None:
        if self.broker_client is None:
            return

        try:
            open_trades = await self.broker_client.get_open_trades()
            if not open_trades:
                return

            logger.warning(
                "weekend_guard_closing",
                trade_count=len(open_trades),
            )

            for trade in open_trades:
                await self.broker_client.close_trade(
                    trade_id=trade["id"],
                    units="ALL",
                )
                logger.info("weekend_guard_closed", trade_id=trade["id"])

        except Exception as exc:
            logger.error("weekend_guard_error", error=str(exc))
