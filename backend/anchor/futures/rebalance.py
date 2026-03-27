"""Portfolio-level futures rebalance planning for Anchor Futures v1."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from anchor.config import get_settings
from anchor.futures.contracts import get_futures_market
from anchor.futures.io import load_daily_market_closes
from anchor.futures.strategy import FuturesTarget, build_futures_v1_targets


@dataclass(frozen=True)
class DesiredFuturesPosition:
    market: str
    instrument: str
    contracts: int
    weight: float
    reference_price: float
    notional_usd: float
    estimated_margin_usd: float


@dataclass(frozen=True)
class FuturesRebalanceAction:
    action: str
    market: str
    instrument: str
    contracts: int
    direction: str
    reason: str


@dataclass(frozen=True)
class FuturesRebalancePlan:
    as_of: str
    equity: float
    desired_positions: list[DesiredFuturesPosition]
    actual_positions: list[dict[str, Any]]
    pending_orders: list[dict[str, Any]]
    actions: list[FuturesRebalanceAction]
    gross_notional_usd: float
    estimated_margin_usd: float
    estimated_margin_usage_pct: float
    blocked_reason: str | None = None


def _desired_contract_count(
    weight: float,
    equity: float,
    reference_price: float,
    point_value_usd: float,
    target_gross_exposure: float,
    max_contracts_per_market: int,
) -> int:
    if reference_price <= 0 or point_value_usd <= 0 or abs(weight) <= 0 or equity <= 0:
        return 0
    notional = reference_price * point_value_usd
    target_notional = abs(weight) * equity * max(target_gross_exposure, 0.0)
    contracts = round(target_notional / notional)
    return max(0, min(max_contracts_per_market, contracts))


def _market_from_instrument(instrument: str) -> str:
    return instrument.strip().upper().split("-", 1)[0]


def _contract_month_key(instrument: str) -> str:
    normalized = instrument.strip().upper()
    if "-" not in normalized:
        return normalized
    market, expiry = normalized.split("-", 1)
    return f"{market}-{expiry[:6]}"


def _pending_markets(pending_orders: list[dict[str, Any]], markets: list[str]) -> set[str]:
    allowed = set(markets)
    return {
        _market_from_instrument(order.get("instrument", ""))
        for order in pending_orders
        if _market_from_instrument(order.get("instrument", "")) in allowed
    }


def build_desired_positions(
    data_dir: str,
    equity: float,
    markets: list[str],
) -> tuple[str, list[DesiredFuturesPosition], float, float, float]:
    settings = get_settings()
    close_frame = load_daily_market_closes(data_dir=data_dir, markets=markets)
    as_of = close_frame.index[-1]
    targets: list[FuturesTarget] = build_futures_v1_targets(data_dir=data_dir, markets=markets, as_of=as_of)
    desired_positions: list[DesiredFuturesPosition] = []
    gross_notional_usd = 0.0
    estimated_margin_usd = 0.0
    for target in targets:
        market = get_futures_market(target.market)
        reference_price = float(close_frame.loc[as_of, target.market])
        contracts = _desired_contract_count(
            weight=target.weight,
            equity=equity,
            reference_price=reference_price,
            point_value_usd=market.point_value_usd,
            target_gross_exposure=settings.futures_target_gross_exposure,
            max_contracts_per_market=settings.futures_max_contracts_per_market,
        )
        if contracts <= 0:
            continue
        notional_usd = reference_price * market.point_value_usd * contracts
        margin_usd = notional_usd * market.margin_requirement_pct_estimate
        desired_positions.append(
            DesiredFuturesPosition(
                market=target.market,
                instrument=target.contract,
                contracts=contracts if target.signal > 0 else -contracts,
                weight=target.weight,
                reference_price=reference_price,
                notional_usd=notional_usd,
                estimated_margin_usd=margin_usd,
            )
        )
        gross_notional_usd += notional_usd
        estimated_margin_usd += margin_usd
    estimated_margin_usage_pct = estimated_margin_usd / equity if equity > 0 else 0.0
    return as_of.isoformat(), desired_positions, gross_notional_usd, estimated_margin_usd, estimated_margin_usage_pct


def build_futures_rebalance_plan(
    data_dir: str,
    equity: float,
    actual_positions: list[dict[str, Any]],
    pending_orders: list[dict[str, Any]],
    markets: list[str],
) -> FuturesRebalancePlan:
    settings = get_settings()
    as_of, desired_positions, gross_notional_usd, estimated_margin_usd, estimated_margin_usage_pct = build_desired_positions(
        data_dir=data_dir,
        equity=equity,
        markets=markets,
    )
    pending_markets = _pending_markets(pending_orders, markets)
    desired_by_market = {position.market: position for position in desired_positions}

    actual_rows = []
    actual_by_market: dict[str, list[dict[str, Any]]] = {}
    for position in actual_positions:
        instrument = str(position.get("instrument", "")).upper()
        market = _market_from_instrument(instrument)
        if market not in set(markets):
            continue
        actual_rows.append(position)
        actual_by_market.setdefault(market, []).append(position)

    blocked_reason: str | None = None
    actions: list[FuturesRebalanceAction] = []
    if estimated_margin_usage_pct > settings.futures_max_margin_usage_pct:
        blocked_reason = (
            f"estimated_margin_usage_pct {estimated_margin_usage_pct:.3f} exceeds "
            f"limit {settings.futures_max_margin_usage_pct:.3f}"
        )
    for market in markets:
        if blocked_reason:
            break
        if market in pending_markets:
            continue

        desired = desired_by_market.get(market)
        actual_market_positions = actual_by_market.get(market, [])

        if desired is None:
            for actual in actual_market_positions:
                contracts = int(round(abs(float(actual.get("currentUnits", 0.0) or 0.0))))
                if contracts <= 0:
                    continue
                actions.append(
                    FuturesRebalanceAction(
                        action="close",
                        market=market,
                        instrument=str(actual["instrument"]).upper(),
                        contracts=contracts,
                        direction="SHORT" if float(actual.get("currentUnits", 0.0) or 0.0) > 0 else "LONG",
                        reason="market_not_in_target_set",
                    )
                )
            continue

        same_contract_units = 0
        for actual in actual_market_positions:
            actual_instrument = str(actual["instrument"]).upper()
            units = int(round(float(actual.get("currentUnits", 0.0) or 0.0)))
            if _contract_month_key(actual_instrument) != _contract_month_key(desired.instrument):
                contracts = abs(units)
                if contracts <= 0:
                    continue
                actions.append(
                    FuturesRebalanceAction(
                        action="close",
                        market=market,
                        instrument=actual_instrument,
                        contracts=contracts,
                        direction="SHORT" if units > 0 else "LONG",
                        reason="roll_or_contract_mismatch",
                    )
                )
            else:
                same_contract_units += units

        delta = desired.contracts - same_contract_units
        if delta != 0:
            actions.append(
                FuturesRebalanceAction(
                    action="open",
                    market=market,
                    instrument=desired.instrument,
                    contracts=abs(delta),
                    direction="LONG" if delta > 0 else "SHORT",
                    reason="target_delta",
                )
            )

    return FuturesRebalancePlan(
        as_of=as_of,
        equity=equity,
        desired_positions=desired_positions,
        actual_positions=actual_rows,
        pending_orders=[order for order in pending_orders if _market_from_instrument(order.get("instrument", "")) in set(markets)],
        actions=actions,
        gross_notional_usd=gross_notional_usd,
        estimated_margin_usd=estimated_margin_usd,
        estimated_margin_usage_pct=estimated_margin_usage_pct,
        blocked_reason=blocked_reason,
    )
