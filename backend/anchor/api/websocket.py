"""
WebSocket hub with Redis pub/sub fanout.

Clients connect to /ws and subscribe to channels.
Engine publishes to Redis, which fans out to all connected WebSocket clients.
"""
import asyncio
import json
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = structlog.get_logger(__name__)

router = APIRouter()

VALID_CHANNELS = {"ticks", "signals", "positions", "orders", "regime", "heartbeat", "account"}


class ConnectionManager:
    def __init__(self):
        self._connections: dict[WebSocket, set[str]] = {}

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections[ws] = set()

    def subscribe(self, ws: WebSocket, channels: list[str]) -> None:
        valid = {c for c in channels if c in VALID_CHANNELS}
        self._connections[ws] = valid

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.pop(ws, None)

    async def broadcast(self, channel: str, data: Any) -> None:
        payload = json.dumps({"channel": channel, "data": data})
        dead = []
        for ws, channels in self._connections.items():
            if channel in channels:
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            try:
                text = await asyncio.wait_for(ws.receive_text(), timeout=30)
                msg = json.loads(text)
                if "subscribe" in msg:
                    manager.subscribe(ws, msg["subscribe"])
                    await ws.send_text(json.dumps({
                        "type": "subscribed",
                        "channels": list(manager._connections.get(ws, set())),
                    }))
            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                await ws.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as exc:
        logger.warning("websocket_error", error=str(exc))
        manager.disconnect(ws)
