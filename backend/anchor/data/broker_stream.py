"""
Streaming market-data adapter placeholder for the IBKR futures rollout.
"""
from __future__ import annotations


class BrokerStreamClient:
    def __init__(self, *args, **kwargs):
        self._running = False

    async def run(self) -> None:
        raise RuntimeError("Broker stream adapter is not configured in this repo.")

    async def stop(self) -> None:
        self._running = False
