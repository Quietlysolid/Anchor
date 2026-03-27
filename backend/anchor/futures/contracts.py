"""Canonical futures universe and contract metadata for Anchor Futures v1."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FuturesContractSpec:
    symbol: str
    exchange: str
    currency: str
    multiplier: str
    tick_size: float
    tick_value_usd: float
    trading_class: str | None = None
    primary_exchange: str | None = None


@dataclass(frozen=True)
class FuturesMarketConfig:
    market_id: str
    description: str
    root_symbol: str
    ibkr_symbol: str
    sec_type: str
    exchange: str
    currency: str
    contract_multiplier: str
    tick_size: float
    tick_value_usd: float
    point_value_usd: float
    micro: bool
    sector: str
    roll_days_before_expiry: int
    contract_months: tuple[str, ...]
    expiry_day_hint: int
    reference_price_symbol: str | None = None
    trading_class: str | None = None
    margin_requirement_pct_estimate: float = 0.10

    @property
    def front_contract(self) -> FuturesContractSpec:
        return FuturesContractSpec(
            symbol=self.ibkr_symbol,
            exchange=self.exchange,
            currency=self.currency,
            multiplier=self.contract_multiplier,
            tick_size=self.tick_size,
            tick_value_usd=self.tick_value_usd,
            trading_class=self.trading_class,
        )


FUTURES_UNIVERSE_V1: tuple[FuturesMarketConfig, ...] = (
    FuturesMarketConfig(
        market_id="MES",
        description="Micro E-mini S&P 500",
        root_symbol="MES",
        ibkr_symbol="MES",
        sec_type="FUT",
        exchange="CME",
        currency="USD",
        contract_multiplier="5",
        tick_size=0.25,
        tick_value_usd=1.25,
        point_value_usd=5.0,
        micro=True,
        sector="equity_index",
        roll_days_before_expiry=5,
        contract_months=("H", "M", "U", "Z"),
        expiry_day_hint=15,
        reference_price_symbol="ES",
        trading_class="MES",
        margin_requirement_pct_estimate=0.12,
    ),
    FuturesMarketConfig(
        market_id="MNQ",
        description="Micro E-mini Nasdaq-100",
        root_symbol="MNQ",
        ibkr_symbol="MNQ",
        sec_type="FUT",
        exchange="CME",
        currency="USD",
        contract_multiplier="2",
        tick_size=0.25,
        tick_value_usd=0.5,
        point_value_usd=2.0,
        micro=True,
        sector="equity_index",
        roll_days_before_expiry=5,
        contract_months=("H", "M", "U", "Z"),
        expiry_day_hint=15,
        reference_price_symbol="NQ",
        trading_class="MNQ",
        margin_requirement_pct_estimate=0.15,
    ),
    FuturesMarketConfig(
        market_id="ZN",
        description="10-Year U.S. Treasury Note",
        root_symbol="ZN",
        ibkr_symbol="ZN",
        sec_type="FUT",
        exchange="CBOT",
        currency="USD",
        contract_multiplier="1000",
        tick_size=0.015625,
        tick_value_usd=15.625,
        point_value_usd=1000.0,
        micro=False,
        sector="rates",
        roll_days_before_expiry=7,
        contract_months=("H", "M", "U", "Z"),
        expiry_day_hint=20,
        trading_class="ZN",
        margin_requirement_pct_estimate=0.04,
    ),
    FuturesMarketConfig(
        market_id="MGC",
        description="Micro Gold",
        root_symbol="MGC",
        ibkr_symbol="MGC",
        sec_type="FUT",
        exchange="COMEX",
        currency="USD",
        contract_multiplier="10",
        tick_size=0.1,
        tick_value_usd=1.0,
        point_value_usd=10.0,
        micro=True,
        sector="metals",
        roll_days_before_expiry=35,
        contract_months=("G", "J", "M", "Q", "V", "Z"),
        expiry_day_hint=25,
        reference_price_symbol="GC",
        trading_class="MGC",
        margin_requirement_pct_estimate=0.08,
    ),
    FuturesMarketConfig(
        market_id="MCL",
        description="Micro WTI Crude Oil",
        root_symbol="MCL",
        ibkr_symbol="MCL",
        sec_type="FUT",
        exchange="NYMEX",
        currency="USD",
        contract_multiplier="100",
        tick_size=0.01,
        tick_value_usd=1.0,
        point_value_usd=100.0,
        micro=True,
        sector="energy",
        roll_days_before_expiry=25,
        contract_months=("F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z"),
        expiry_day_hint=20,
        reference_price_symbol="CL",
        trading_class="MCL",
        margin_requirement_pct_estimate=0.18,
    ),
)


_FUTURES_BY_ID = {market.market_id: market for market in FUTURES_UNIVERSE_V1}


def get_futures_universe_v1() -> list[FuturesMarketConfig]:
    return list(FUTURES_UNIVERSE_V1)


def get_futures_market(market_id: str) -> FuturesMarketConfig:
    normalized = market_id.strip().upper()
    try:
        return _FUTURES_BY_ID[normalized]
    except KeyError as exc:
        raise KeyError(f"Unknown futures market '{market_id}'. Known: {sorted(_FUTURES_BY_ID)}") from exc
