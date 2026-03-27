from __future__ import annotations

from dataclasses import dataclass

from anchor.config import get_settings
from anchor.futures.contracts import get_futures_market
from anchor.futures.ibkr_contracts import resolve_futures_contract

settings = get_settings()


@dataclass(frozen=True)
class ParsedInstrument:
    raw: str
    symbol: str
    currency: str
    sec_type: str
    exchange: str
    multiplier: str | None = None
    last_trade_date_or_contract_month: str | None = None
    trading_class: str | None = None


def normalize_instrument_symbol(instrument: str) -> str:
    return instrument.replace(".", "_").upper()


def parse_ibkr_instrument(instrument: str) -> ParsedInstrument:
    normalized = normalize_instrument_symbol(instrument)
    try:
        get_futures_market(normalized.split("-", 1)[0])
        is_futures = True
    except KeyError:
        is_futures = False
    if is_futures:
        futures_contract = resolve_futures_contract(normalized)
        return ParsedInstrument(
            raw=normalized,
            symbol=futures_contract.ibkr_symbol,
            currency=futures_contract.currency,
            sec_type=futures_contract.sec_type,
            exchange=futures_contract.exchange,
            multiplier=futures_contract.multiplier,
            last_trade_date_or_contract_month=futures_contract.last_trade_date_or_contract_month,
            trading_class=futures_contract.trading_class,
        )
    if "_" in normalized:
        base, quote = normalized.split("_", 1)
        return ParsedInstrument(
            raw=normalized,
            symbol=base,
            currency=quote,
            sec_type="CASH",
            exchange=settings.ibkr_exchange_forex,
        )

    # Phase-1 crypto support assumes USD spot crypto.
    return ParsedInstrument(
        raw=normalized,
        symbol=normalized,
        currency="USD",
        sec_type="CRYPTO",
        exchange=settings.ibkr_exchange_crypto,
    )


def ibkr_position_id(instrument: str) -> str:
    return normalize_instrument_symbol(instrument)
