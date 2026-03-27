"""Broker client facade."""
from __future__ import annotations

import threading

from anchor.execution.ibkr_client import IBKRBrokerClient


class BrokerClient:
    _instance: IBKRBrokerClient | None = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = IBKRBrokerClient()
        return cls._instance
