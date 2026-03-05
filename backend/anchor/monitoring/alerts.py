"""
Alert routing — Telegram bot primary, can extend to email/Slack.
"""
import httpx
import structlog

from anchor.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


class AlertService:
    async def send_critical(self, message: str) -> None:
        await self._telegram(f"🚨 CRITICAL\n{message}")
        logger.critical("alert_sent", message=message)

    async def send_warning(self, message: str) -> None:
        await self._telegram(f"⚠️ WARNING\n{message}")
        logger.warning("alert_sent", message=message)

    async def send_info(self, message: str) -> None:
        await self._telegram(f"ℹ️ INFO\n{message}")

    async def _telegram(self, text: str) -> None:
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            logger.warning("telegram_not_configured")
            return

        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, json={
                    "chat_id":    settings.telegram_chat_id,
                    "text":       text[:4096],
                    "parse_mode": "HTML",
                })
        except Exception as exc:
            logger.error("telegram_send_failed", error=str(exc))
