"""
Watchdog — runs as a SEPARATE process from the engine.
Monitors heartbeat freshness, engine health, broker sync, and recent risk/error events.

Run as: python -m anchor.monitoring.watchdog
"""
import asyncio
from datetime import datetime, timedelta

import httpx
from sqlalchemy import desc, or_, select

import structlog

from anchor.config import get_settings
from anchor.database.engine import init_db
from anchor.database.models import SystemEvent
from anchor.monitoring.alerts import AlertService
from anchor.utils.time_utils import utcnow

logger = structlog.get_logger(__name__)
settings = get_settings()


class WatchdogService:
    def __init__(self, alert_service: AlertService | None = None):
        self.alert_service = alert_service or AlertService()
        self._last_alerted_at: dict[str, datetime] = {}

    async def run(self) -> None:
        logger.info("watchdog_started")
        while True:
            try:
                await self._check()
            except Exception as exc:
                logger.error("watchdog_check_error", error=str(exc))
            await asyncio.sleep(settings.watchdog_check_interval_seconds)

    async def _send_once(self, key: str, severity: str, message: str) -> None:
        now = utcnow()
        last_sent = self._last_alerted_at.get(key)
        if last_sent is not None and (now - last_sent).total_seconds() < settings.watchdog_alert_cooldown_seconds:
            return

        if severity == "critical":
            await self.alert_service.send_critical(message)
        elif severity == "warning":
            await self.alert_service.send_warning(message)
        else:
            await self.alert_service.send_info(message)
        self._last_alerted_at[key] = now

    async def _fetch_engine_health(self) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(settings.watchdog_health_endpoint_url)
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            logger.warning("watchdog_health_fetch_failed", error=str(exc))
            await self._send_once(
                "engine_health_unreachable",
                "critical",
                f"WATCHDOG: Engine health endpoint unreachable at {settings.watchdog_health_endpoint_url}.",
            )
            return None

    async def _check(self) -> None:
        # Fresh session per check — long-lived sessions idle-timeout on Postgres and
        # silently stop working, which would cause the watchdog to never fire alerts.
        from anchor.database.engine import AsyncSessionFactory
        from anchor.database.repositories.events import SystemEventRepository

        async with AsyncSessionFactory() as session:
            event_repo = SystemEventRepository(session)
            latest_hb = await event_repo.get_latest_heartbeat()
            recent_events = (
                await session.execute(
                    select(SystemEvent)
                    .where(
                        SystemEvent.event_at >= utcnow() - timedelta(minutes=settings.watchdog_recent_event_lookback_minutes),
                        or_(
                            SystemEvent.event_type.in_(["FUTURES_MARGIN_GUARD", "FUTURES_DRAWDOWN_GUARD"]),
                            SystemEvent.severity.in_(["ERROR", "CRITICAL"]),
                        ),
                    )
                    .order_by(desc(SystemEvent.event_at))
                    .limit(20)
                )
            ).scalars().all()

        if latest_hb is None:
            await self._send_once(
                "heartbeat_missing",
                "critical",
                "WATCHDOG: No heartbeat found in database. Engine may not have started."
            )
            return

        age = (utcnow() - latest_hb.event_at).total_seconds()
        if age > settings.watchdog_heartbeat_stale_seconds:
            await self._send_once(
                "heartbeat_stale",
                "critical",
                f"WATCHDOG: Heartbeat is {int(age)}s old (threshold: {settings.watchdog_heartbeat_stale_seconds}s). "
                f"Engine may be down. Last seen: {latest_hb.event_at.isoformat()}"
            )
            return

        health = await self._fetch_engine_health()
        if health is not None:
            await self._check_engine_health(health)

        await self._check_recent_events(recent_events)
        logger.debug("watchdog_ok", heartbeat_age_seconds=age)

    async def _check_engine_health(self, health: dict) -> None:
        status = str(health.get("status") or "").lower()
        stream_connected = bool(health.get("stream_connected"))
        market_data_mode = str(health.get("market_data_mode") or "unknown")
        open_positions = int(health.get("open_positions") or 0)
        last_reconciliation = health.get("last_reconciliation")

        if status != "ok":
            await self._send_once(
                "engine_degraded",
                "critical",
                f"WATCHDOG: Engine health is {status}. Check Anchor immediately.",
            )

        if not stream_connected:
            await self._send_once(
                "broker_stream_disconnected",
                "critical",
                "WATCHDOG: Broker stream is disconnected. Live account state may be stale.",
            )

        if settings.watchdog_warn_on_delayed_market_data and market_data_mode != "live" and open_positions > 0:
            await self._send_once(
                f"market_data_mode_{market_data_mode}",
                "warning",
                f"WATCHDOG: Market data mode is {market_data_mode} while positions are open.",
            )

        if last_reconciliation:
            try:
                age = (utcnow() - datetime.fromisoformat(last_reconciliation)).total_seconds()
            except Exception:
                age = 0
            if age > settings.watchdog_broker_sync_critical_seconds:
                await self._send_once(
                    "broker_sync_critical",
                    "critical",
                    f"WATCHDOG: Broker reconciliation is critically stale by {int(age)}s.",
                )
            elif age > settings.watchdog_broker_sync_stale_seconds:
                await self._send_once(
                    "broker_sync_stale",
                    "warning",
                    f"WATCHDOG: Broker reconciliation is stale by {int(age)}s.",
                )

    async def _check_recent_events(self, recent_events: list[SystemEvent]) -> None:
        for event in recent_events:
            event_key = f"event_{event.id}"
            if event.event_type == "FUTURES_DRAWDOWN_GUARD":
                await self._send_once(
                    event_key,
                    "critical",
                    f"WATCHDOG: {event.message}",
                )
                continue
            if event.event_type == "FUTURES_MARGIN_GUARD":
                await self._send_once(
                    event_key,
                    "warning",
                    f"WATCHDOG: {event.message}",
                )
                continue

            severity = getattr(event.severity, "value", str(event.severity)).upper()
            if severity in {"ERROR", "CRITICAL"}:
                await self._send_once(
                    event_key,
                    "critical",
                    f"WATCHDOG: {event.message}",
                )
            elif severity in {"WARNING", "WARN"}:
                await self._send_once(
                    event_key,
                    "warning",
                    f"WATCHDOG: {event.message}",
                )


async def main() -> None:
    from anchor.utils.logging import configure_logging
    configure_logging(settings.log_level)

    await init_db()

    watchdog = WatchdogService()
    await watchdog.run()


if __name__ == "__main__":
    asyncio.run(main())
