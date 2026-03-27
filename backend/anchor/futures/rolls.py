"""Deterministic futures roll helpers for Anchor Futures v1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from anchor.futures.contracts import FuturesMarketConfig, get_futures_market

_MONTH_NUMBER_BY_CODE = {
    "F": 1,
    "G": 2,
    "H": 3,
    "J": 4,
    "K": 5,
    "M": 6,
    "N": 7,
    "Q": 8,
    "U": 9,
    "V": 10,
    "X": 11,
    "Z": 12,
}


@dataclass(frozen=True)
class FuturesRollDecision:
    market_id: str
    contract_month: str
    roll_trigger_date: date
    expiry_hint_date: date
    local_symbol: str


def _coerce_as_of(as_of: date | datetime | None) -> date:
    if as_of is None:
        return datetime.now(timezone.utc).date()
    if isinstance(as_of, datetime):
        return as_of.date()
    return as_of


def _candidate_contracts(
    market: FuturesMarketConfig,
    *,
    as_of: date,
    years_forward: int = 2,
) -> list[tuple[str, date, date]]:
    candidates: list[tuple[str, date, date]] = []
    start_year = as_of.year
    end_year = start_year + years_forward
    for year in range(start_year, end_year + 1):
        for month_code in market.contract_months:
            month_number = _MONTH_NUMBER_BY_CODE[month_code]
            expiry_hint = date(year, month_number, market.expiry_day_hint)
            roll_trigger = expiry_hint - timedelta(days=market.roll_days_before_expiry)
            contract_month = f"{year}{month_number:02d}"
            candidates.append((contract_month, roll_trigger, expiry_hint))
    return sorted(candidates, key=lambda row: row[0])


def resolve_front_contract_month(market_id: str, *, as_of: date | datetime | None = None) -> FuturesRollDecision:
    market = get_futures_market(market_id)
    current_date = _coerce_as_of(as_of)
    for contract_month, roll_trigger, expiry_hint in _candidate_contracts(market, as_of=current_date):
        if roll_trigger > current_date:
            return FuturesRollDecision(
                market_id=market.market_id,
                contract_month=contract_month,
                roll_trigger_date=roll_trigger,
                expiry_hint_date=expiry_hint,
                local_symbol=f"{market.market_id}-{contract_month}",
            )
    raise RuntimeError(f"No candidate futures contract found for {market_id} as of {current_date.isoformat()}")
