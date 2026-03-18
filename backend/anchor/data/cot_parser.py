"""CFTC Commitments of Traders (COT) report parser.

Downloads the weekly Disaggregated Futures report (free, no API key needed).
Extracts commercial/non-commercial net positioning for major currencies.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from typing import Dict, Optional

import httpx
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# CFTC Financial Futures Disaggregated — annual zip, includes FX (CME currency futures)
# URL pattern: https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip
def _cot_url() -> str:
    return f"https://www.cftc.gov/files/dea/history/fut_fin_txt_{datetime.now().year}.zip"

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

# Disaggregated format: Leveraged Money = speculative non-commercial; Dealer = commercial
COLUMNS_KEEP = [
    "Market_and_Exchange_Names",
    "As_of_Date_In_Form_YYMMDD",
    "Lev_Money_Positions_Long_All",
    "Lev_Money_Positions_Short_All",
    "Dealer_Positions_Long_All",
    "Dealer_Positions_Short_All",
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
            resp = await self._client.get(_cot_url())
            resp.raise_for_status()
        except Exception as exc:
            logger.error("cot_fetch_failed", error=str(exc))
            return {}

        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                fname = next(n for n in zf.namelist() if n.endswith(".txt"))
                with zf.open(fname) as f:
                    df = pd.read_csv(f, usecols=COLUMNS_KEEP, low_memory=False)
        except Exception as exc:
            logger.error("cot_parse_failed", error=str(exc))
            return {}

        result: Dict[str, Dict] = {}
        for contract_name, currency in CONTRACT_MAP.items():
            rows = df[df["Market_and_Exchange_Names"].str.contains(contract_name, case=False, na=False)]
            if rows.empty:
                continue
            latest = rows.iloc[0]   # CFTC CSV is newest-first; iloc[0] = most recent week
            # Disaggregated format: Leveraged Money ≈ non-commercial speculators; Dealer ≈ commercial
            net_noncomm = (
                latest["Lev_Money_Positions_Long_All"] - latest["Lev_Money_Positions_Short_All"]
            )
            net_comm = (
                latest["Dealer_Positions_Long_All"] - latest["Dealer_Positions_Short_All"]
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
