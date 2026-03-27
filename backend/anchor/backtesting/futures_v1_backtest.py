"""Anchor Futures v1 daily trend backtest."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from anchor.futures.contracts import get_futures_market
from anchor.futures.ibkr_contracts import resolve_futures_contract
from anchor.futures.io import load_daily_market_closes


def _weekly_rebalance_index(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    series = pd.Series(index, index=index)
    return pd.DatetimeIndex(series.resample("W-FRI").last().dropna().tolist())


def _monthly_rebalance_index(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    series = pd.Series(index, index=index)
    return pd.DatetimeIndex(series.resample("ME").last().dropna().tolist())


def _trend_signal(close_frame: pd.DataFrame, lookback_days: int, threshold: float) -> pd.DataFrame:
    trailing_return = close_frame / close_frame.shift(lookback_days) - 1.0
    signal = pd.DataFrame(0.0, index=trailing_return.index, columns=trailing_return.columns)
    signal = signal.mask(trailing_return > threshold, 1.0)
    signal = signal.mask(trailing_return < -threshold, -1.0)
    return signal


def _daily_metrics(daily_returns: pd.Series, initial_balance: float) -> dict[str, Any]:
    equity = initial_balance * (1.0 + daily_returns.fillna(0.0)).cumprod()
    peak = equity.cummax()
    drawdown = (equity - peak) / peak

    gross_wins = daily_returns[daily_returns > 0].sum()
    gross_losses = abs(daily_returns[daily_returns < 0].sum())
    pf = float(gross_wins / gross_losses) if gross_losses > 0 else float("inf")
    ann_vol = float(daily_returns.std(ddof=0) * np.sqrt(252)) if len(daily_returns) > 1 else 0.0
    ann_ret = (
        float(((equity.iloc[-1] / initial_balance) ** (252 / max(len(daily_returns), 1))) - 1)
        if not equity.empty
        else 0.0
    )
    sharpe = (
        float(daily_returns.mean() / daily_returns.std(ddof=0) * np.sqrt(252))
        if daily_returns.std(ddof=0) > 0
        else 0.0
    )
    return {
        "days": int(len(daily_returns)),
        "final_balance": round(float(equity.iloc[-1]) if not equity.empty else initial_balance, 2),
        "net_return_pct": round((float(equity.iloc[-1]) / initial_balance - 1.0) * 100 if not equity.empty else 0.0, 2),
        "annualized_return_pct": round(ann_ret * 100, 2),
        "annualized_vol_pct": round(ann_vol * 100, 2),
        "sharpe": round(sharpe, 3),
        "profit_factor": round(pf, 3),
        "max_drawdown_pct": round(float(drawdown.min()) * 100 if not drawdown.empty else 0.0, 2),
        "positive_days_pct": round(float((daily_returns > 0).mean() * 100), 2) if len(daily_returns) else 0.0,
    }


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

    vol = history.std(ddof=0).replace(0.0, np.nan)
    inv_vol = (1.0 / vol).replace([np.inf, -np.inf], np.nan).dropna()
    if inv_vol.empty:
        equal = 1.0 / len(active_markets)
        return {market: equal for market in active_markets}
    inv_vol /= inv_vol.sum()
    return {market: float(inv_vol.get(market, 0.0)) for market in active_markets}


def _turnover_cost_rate(
    previous_weights: dict[str, float],
    new_weights: dict[str, float],
    transaction_cost_bps: float,
) -> float:
    if transaction_cost_bps <= 0:
        return 0.0
    markets = set(previous_weights) | set(new_weights)
    turnover = sum(abs(new_weights.get(market, 0.0) - previous_weights.get(market, 0.0)) for market in markets)
    return (transaction_cost_bps / 10_000.0) * turnover


def _roll_cost_rate(
    date_key: pd.Timestamp,
    active_weights: dict[str, float],
    active_contracts: dict[str, str],
    roll_cost_bps: float,
) -> tuple[float, dict[str, str]]:
    if roll_cost_bps <= 0 or not active_weights:
        return 0.0, active_contracts

    updated_contracts = dict(active_contracts)
    cost = 0.0
    for market, weight in active_weights.items():
        contract = resolve_futures_contract(market, as_of=date_key.to_pydatetime()).local_symbol
        previous_contract = updated_contracts.get(market)
        if previous_contract is not None and previous_contract != contract:
            cost += (roll_cost_bps / 10_000.0) * abs(weight)
        updated_contracts[market] = contract
    return cost, updated_contracts


def run_futures_v1_backtest(
    data_dir: str,
    markets: list[str],
    initial_balance: float,
    trend_lookback_days: int = 126,
    vol_lookback_days: int = 20,
    rebalance_frequency: str = "weekly",
    threshold: float = 0.0,
    transaction_cost_bps: float = 0.0,
    roll_cost_bps: float = 0.0,
) -> tuple[dict[str, Any], pd.DataFrame]:
    closes = load_daily_market_closes(data_dir=data_dir, markets=markets)
    returns = closes.pct_change(fill_method=None).fillna(0.0)
    signals = _trend_signal(closes, lookback_days=trend_lookback_days, threshold=threshold)

    if rebalance_frequency == "weekly":
        rebalance_index = _weekly_rebalance_index(closes.index)
    else:
        rebalance_index = _monthly_rebalance_index(closes.index)

    active_weights: dict[str, float] = {}
    active_contracts: dict[str, str] = {}
    previous_weights: dict[str, float] = {}
    rows: list[dict[str, Any]] = []
    portfolio_returns: dict[pd.Timestamp, float] = {}

    for date_key in closes.index:
        daily_cost = 0.0
        if date_key in rebalance_index:
            current_signal = signals.loc[date_key].dropna()
            active_markets = [market for market, signal in current_signal.items() if abs(signal) > 0]
            if active_markets:
                base_weights = _inverse_vol_weights(
                    returns_frame=returns,
                    as_of=date_key,
                    active_markets=active_markets,
                    lookback_days=vol_lookback_days,
                )
                active_weights = {
                    market: base_weights[market] * float(current_signal[market])
                    for market in active_markets
                }
                daily_cost += _turnover_cost_rate(
                    previous_weights=previous_weights,
                    new_weights=active_weights,
                    transaction_cost_bps=transaction_cost_bps,
                )
                previous_weights = dict(active_weights)
                rows.append(
                    {
                        "date": date_key,
                        "weights": dict(sorted(active_weights.items())),
                        "signals": {market: float(current_signal[market]) for market in active_markets},
                    }
                )
            else:
                daily_cost += _turnover_cost_rate(
                    previous_weights=previous_weights,
                    new_weights={},
                    transaction_cost_bps=transaction_cost_bps,
                )
                active_weights = {}
                active_contracts = {}
                previous_weights = {}

        if not active_weights:
            continue

        roll_cost, active_contracts = _roll_cost_rate(
            date_key=date_key,
            active_weights=active_weights,
            active_contracts=active_contracts,
            roll_cost_bps=roll_cost_bps,
        )
        daily_cost += roll_cost
        realized = returns.loc[date_key]
        gross_return = float(
            sum(active_weights.get(market, 0.0) * realized.get(market, 0.0) for market in realized.index)
        )
        portfolio_returns[date_key] = gross_return - daily_cost

    portfolio_series = pd.Series(portfolio_returns).sort_index()
    equity_curve = initial_balance * (1.0 + portfolio_series.fillna(0.0)).cumprod()
    equity_by_day = pd.DataFrame(
        {
            "portfolio_return": portfolio_series,
            "equity": equity_curve.reindex(portfolio_series.index),
        }
    )

    result = {
        "markets": markets,
        "market_specs": {
            market: {
                "description": get_futures_market(market).description,
                "sector": get_futures_market(market).sector,
                "tick_size": get_futures_market(market).tick_size,
                "tick_value_usd": get_futures_market(market).tick_value_usd,
            }
            for market in markets
        },
        "parameters": {
            "trend_lookback_days": trend_lookback_days,
            "vol_lookback_days": vol_lookback_days,
            "rebalance_frequency": rebalance_frequency,
            "threshold": threshold,
            "weighting": "inverse_volatility",
            "signal_rule": "sign_of_trailing_return",
            "transaction_cost_bps": transaction_cost_bps,
            "roll_cost_bps": roll_cost_bps,
        },
        "metrics": _daily_metrics(portfolio_series, initial_balance=initial_balance),
        "rebalances": [
            {
                "date": row["date"].date().isoformat(),
                "weights": {market: round(weight, 6) for market, weight in row["weights"].items()},
                "signals": row["signals"],
            }
            for row in rows[-12:]
        ],
    }
    return result, equity_by_day


def main() -> None:
    parser = argparse.ArgumentParser(description="Anchor Futures v1 daily trend backtest")
    parser.add_argument("--data-dir", default="/app/data", help="Directory containing <MARKET>_D.csv files")
    parser.add_argument("--markets", default="MES,MNQ,ZN,MGC,MCL", help="Comma-separated futures market ids")
    parser.add_argument("--balance", type=float, default=100000.0)
    parser.add_argument("--trend-lookback-days", type=int, default=126)
    parser.add_argument("--vol-lookback-days", type=int, default=20)
    parser.add_argument("--rebalance-frequency", choices=["weekly", "monthly"], default="weekly")
    parser.add_argument("--threshold", type=float, default=0.0, help="Flat zone around zero trailing return")
    parser.add_argument("--transaction-cost-bps", type=float, default=0.0)
    parser.add_argument("--roll-cost-bps", type=float, default=0.0)
    parser.add_argument("--export-json", default=None)
    parser.add_argument("--export-daily", default=None)
    args = parser.parse_args()

    markets = [part.strip().upper() for part in args.markets.split(",") if part.strip()]
    result, equity_by_day = run_futures_v1_backtest(
        data_dir=args.data_dir,
        markets=markets,
        initial_balance=args.balance,
        trend_lookback_days=args.trend_lookback_days,
        vol_lookback_days=args.vol_lookback_days,
        rebalance_frequency=args.rebalance_frequency,
        threshold=args.threshold,
        transaction_cost_bps=args.transaction_cost_bps,
        roll_cost_bps=args.roll_cost_bps,
    )

    print(json.dumps(result, indent=2))

    if args.export_json:
        Path(args.export_json).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.export_daily:
        export = equity_by_day.reset_index().rename(columns={"index": "date"})
        export.to_csv(args.export_daily, index=False)


if __name__ == "__main__":
    main()
