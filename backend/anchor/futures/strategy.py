"""Signal and target generation for Anchor Futures v1."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from anchor.config import get_settings
from anchor.futures.ibkr_contracts import resolve_futures_contract
from anchor.futures.io import load_daily_market_closes


@dataclass(frozen=True)
class FuturesTarget:
    market: str
    contract: str
    signal: float
    weight: float


def _trend_signal(close_frame: pd.DataFrame, lookback_days: int, threshold: float) -> pd.DataFrame:
    trailing_return = close_frame / close_frame.shift(lookback_days) - 1.0
    signal = pd.DataFrame(0.0, index=trailing_return.index, columns=trailing_return.columns)
    signal = signal.mask(trailing_return > threshold, 1.0)
    signal = signal.mask(trailing_return < -threshold, -1.0)
    return signal


def _inverse_vol_weights(
    returns_frame: pd.DataFrame,
    as_of: pd.Timestamp,
    active_markets: list[str],
    lookback_days: int,
) -> dict[str, float]:
    history = returns_frame.loc[returns_frame.index < as_of, active_markets].tail(lookback_days)
    if history.empty:
        equal = 1.0 / len(active_markets)
        return {market: equal for market in active_markets}
    vol = history.std(ddof=0).replace(0.0, pd.NA).dropna()
    if vol.empty:
        equal = 1.0 / len(active_markets)
        return {market: equal for market in active_markets}
    inv_vol = (1.0 / vol).astype(float)
    inv_vol /= inv_vol.sum()
    return {market: float(inv_vol.get(market, 0.0)) for market in active_markets}


def build_futures_v1_targets(
    data_dir: str,
    markets: list[str] | None = None,
    as_of: pd.Timestamp | None = None,
) -> list[FuturesTarget]:
    settings = get_settings()
    markets = markets or list(settings.futures_v1_markets)
    closes = load_daily_market_closes(data_dir=data_dir, markets=markets)
    returns = closes.pct_change(fill_method=None).fillna(0.0)
    signals = _trend_signal(
        closes,
        lookback_days=settings.futures_v1_trend_lookback_days,
        threshold=settings.futures_signal_threshold,
    )

    if as_of is None:
        as_of = closes.index[-1]
    if as_of not in closes.index:
        closes = closes.loc[closes.index <= as_of]
        returns = returns.loc[returns.index <= as_of]
        signals = signals.loc[signals.index <= as_of]
        as_of = closes.index[-1]

    current_signal = signals.loc[as_of].dropna()
    active_markets = [market for market, signal in current_signal.items() if abs(signal) > 0]
    if not active_markets:
        return []

    base_weights = _inverse_vol_weights(
        returns_frame=returns,
        as_of=as_of,
        active_markets=active_markets,
        lookback_days=settings.futures_vol_lookback_days,
    )
    targets = []
    for market in active_markets:
        contract = resolve_futures_contract(market, as_of=as_of.to_pydatetime()).local_symbol
        targets.append(
            FuturesTarget(
                market=market,
                contract=contract,
                signal=float(current_signal[market]),
                weight=float(base_weights[market] * current_signal[market]),
            )
        )
    return sorted(targets, key=lambda item: item.market)
