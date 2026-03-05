"""CFTC Commitments of Traders (COT) report parser.

Downloads the weekly Disaggregated Futures report (free, no API key needed).
Extracts commercial/non-commercial net positioning for major currencies.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timezone
from typing import Dict, Optional

import httpx
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# CFTC publishes legacy futures-only CSV weekly
COT_URL = "https://www.cftc.gov/dea/newcot/f_disagg.zip"

# Map CFTC contract names → our currency codes
CONTRACT_MAP = {
    "EURO FX": "EUR",
    "BRITISH POUND": "GBP",
    "JAPANESE YEN": "JPY",
    "SWISS FRANC": "CHF",
    "AUSTRALIAN DOLLAR": "AUD",
    "NEW ZEALAND DOLLAR": "NZD",
    "CANADIAN DOLLAR": "CAD",
}

COLUMNS_KEEP = [
    "Market_and_Exchange_Names",
    "As_of_Date_In_Form_YYMMDD",
    "Noncomm_Positions_Long_All",
    "Noncomm_Positions_Short_All",
    "Comm_Positions_Long_All",
    "Comm_Positions_Short_All",
]


class CotParser:
    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "CotParser":
        self._client = httpx.AsyncClient(timeout=60.0)
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    async def fetch_latest(self) -> Dict[str, Dict]:
        """Return {currency: {net_noncomm, net_comm, report_date}}."""
        assert self._client is not None
        try:
            resp = await self._client.get(COT_URL)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("cot_fetch_failed", error=str(exc))
            return {}

        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                csv_name = next(n for n in zf.namelist() if n.endswith(".txt"))
                with zf.open(csv_name) as f:
                    df = pd.read_csv(f, usecols=COLUMNS_KEEP, low_memory=False)
        except Exception as exc:
            logger.error("cot_parse_failed", error=str(exc))
            return {}

        result: Dict[str, Dict] = {}
        for contract_name, currency in CONTRACT_MAP.items():
            rows = df[df["Market_and_Exchange_Names"].str.contains(contract_name, case=False, na=False)]
            if rows.empty:
                continue
            latest = rows.iloc[-1]
            net_noncomm = (
                latest["Noncomm_Positions_Long_All"] - latest["Noncomm_Positions_Short_All"]
            )
            net_comm = (
                latest["Comm_Positions_Long_All"] - latest["Comm_Positions_Short_All"]
            )
            try:
                report_date = datetime.strptime(
                    str(int(latest["As_of_Date_In_Form_YYMMDD"])), "%y%m%d"
                ).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                report_date = None

            result[currency] = {
                "net_noncommercial": int(net_noncomm),
                "net_commercial": int(net_comm),
                "report_date": report_date,
            }

        logger.info("cot_fetched", currencies=list(result.keys()))
        return result
