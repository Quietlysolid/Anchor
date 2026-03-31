"""
Historical market-data adapter placeholder for the IBKR futures rollout.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd


class BrokerHistoryClient:
    async def fetch_candles(
        self,
        instrument: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        raise RuntimeError("Broker history import is not configured in this repo.")
