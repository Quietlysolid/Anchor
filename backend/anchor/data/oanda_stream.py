"""
OANDA streaming price consumer.
Connects to the /accounts/{id}/pricing/stream endpoint and writes
ticks to the database + publishes to Redis pub/sub for live dashboard.
"""
import asyncio
import json
from datetime import datetime

import httpx
import structlog

from anchor.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


class OANDAStreamClient:
    def __init__(self, redis_client=None, tick_repo=None):
        self.redis = redis_client
        self.tick_repo = tick_repo
        self._running = False
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._stream()
                self._reconnect_delay = 1.0  # reset on clean disconnect
            except Exception as exc:
                logger.error(
                    "stream_error",
                    error=str(exc),
                    reconnect_in=self._reconnect_delay,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay
                )

    async def stop(self) -> None:
        self._running = False

    async def _stream(self) -> None:
        instruments = ",".join(settings.instruments)
        url = (
            f"{settings.oanda_stream_url}/v3/accounts/"
            f"{settings.oanda_account_id}/pricing/stream"
            f"?instruments={instruments}"
        )
        headers = {"Authorization": f"Bearer {settings.oanda_api_key}"}

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                resp.raise_for_status()
                logger.info("stream_connected", instruments=instruments)

                # Application-layer heartbeat tracking
                last_message_at = asyncio.get_event_loop().time()

                async for line in resp.aiter_lines():
                    if not self._running:
                        return

                    now = asyncio.get_event_loop().time()
                    # Zombie connection detection: no message in 30s
                    if now - last_message_at > 30:
                        logger.warning("stream_zombie_detected")
                        return  # triggers reconnect

                    last_message_at = now

                    if not line.strip():
                        continue

                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg_type = msg.get("type")

                    if msg_type == "PRICE":
                        await self._handle_price(msg)
                    elif msg_type == "HEARTBEAT":
                        pass  # OANDA heartbeat, reset zombie timer above

    async def _handle_price(self, msg: dict) -> None:
        instrument = msg.get("instrument", "")
        time_str   = msg.get("time", "")
        bids       = msg.get("bids", [])
        asks       = msg.get("asks", [])

        if not bids or not asks:
            return

        bid = float(bids[0]["price"])
        ask = float(asks[0]["price"])

        tick = {
            "instrument": instrument,
            "bid": bid,
            "ask": ask,
            "mid": (bid + ask) / 2,
            "spread": ask - bid,
            "time": time_str,
        }

        # Publish to Redis for WebSocket fanout
        if self.redis:
            await self.redis.publish(
                "ticks",
                json.dumps({"channel": "ticks", "data": tick}),
            )

        # Write to DB (batched via repo)
        if self.tick_repo:
            await self.tick_repo.insert_tick(
                instrument=instrument,
                bid=bid,
                ask=ask,
                time=datetime.fromisoformat(time_str.replace("Z", "+00:00")),
            )
