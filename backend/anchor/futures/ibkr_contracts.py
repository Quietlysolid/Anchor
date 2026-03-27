"""IBKR-facing futures contract helpers."""
from __future__ import annotations

from datetime import date, datetime
from dataclasses import dataclass

from anchor.futures.contracts import FuturesMarketConfig, get_futures_market
from anchor.futures.rolls import resolve_front_contract_month


@dataclass(frozen=True)
class FuturesContractSelection:
    market_id: str
    local_symbol: str
    ibkr_symbol: str
    sec_type: str
    exchange: str
    currency: str
    multiplier: str
    last_trade_date_or_contract_month: str | None
    trading_class: str | None


def parse_futures_local_symbol(instrument: str) -> tuple[str, str | None]:
    normalized = instrument.strip().upper()
    if "-" not in normalized:
        return normalized, None
    market_id, contract_month = normalized.split("-", 1)
    return market_id, contract_month or None


def resolve_futures_contract(
    instrument: str,
    *,
    as_of: date | datetime | None = None,
) -> FuturesContractSelection:
    market_id, contract_month = parse_futures_local_symbol(instrument)
    market: FuturesMarketConfig = get_futures_market(market_id)
    if not contract_month:
        contract_month = resolve_front_contract_month(market_id, as_of=as_of).contract_month
    return FuturesContractSelection(
        market_id=market.market_id,
        local_symbol=f"{market.market_id}-{contract_month}",
        ibkr_symbol=market.ibkr_symbol,
        sec_type=market.sec_type,
        exchange=market.exchange,
        currency=market.currency,
        multiplier=market.contract_multiplier,
        last_trade_date_or_contract_month=contract_month,
        trading_class=market.trading_class,
    )
