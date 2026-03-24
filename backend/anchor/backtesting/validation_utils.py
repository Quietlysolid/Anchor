"""Shared statistical validation helpers for backtests."""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd


def _normalized_trade_returns(pnls: np.ndarray, target_risk: float = 0.01) -> np.ndarray:
    """Convert trade P&Ls into approximate fixed-risk returns.

    Most strategy backtests in this repo use roughly fixed-fractional sizing, but the
    stored trade logs often contain absolute P&Ls. We normalize by average absolute
    trade size so a typical trade is on the order of `target_risk`.
    """
    arr = np.asarray(pnls, dtype=float)
    if arr.size == 0:
        return arr
    scale = max(float(np.mean(np.abs(arr))), 1e-8)
    return arr / scale * target_risk


def compute_trade_metrics(pnls: np.ndarray) -> dict[str, float]:
    """Compute trade-level performance metrics from absolute trade P&Ls."""
    arr = np.asarray(pnls, dtype=float)
    if arr.size == 0:
        return {
            "n_trades": 0.0,
            "wr": 0.0,
            "pf": 0.0,
            "mean_pl": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "maxdd": 0.0,
        }

    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    returns = _normalized_trade_returns(arr)

    wr = float(np.mean(arr > 0))
    pf = float(wins.sum() / abs(losses.sum())) if losses.size > 0 and abs(losses.sum()) > 1e-12 else float("inf")
    mean_pl = float(np.mean(arr))

    if returns.size > 1 and float(np.std(returns, ddof=1)) > 1e-12:
        sharpe = float(np.mean(returns) / np.std(returns, ddof=1) * np.sqrt(12 * 20))
    else:
        sharpe = 0.0

    neg = returns[returns < 0]
    if neg.size > 1 and float(np.std(neg, ddof=1)) > 1e-12:
        sortino = float(np.mean(returns) / np.std(neg, ddof=1) * np.sqrt(12 * 20))
    else:
        sortino = 0.0

    equity = np.cumprod(1.0 + returns)
    equity = np.concatenate([[1.0], equity])
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / np.maximum(peak, 1e-12)
    maxdd = abs(float(np.min(drawdown)))

    return {
        "n_trades": float(arr.size),
        "wr": wr,
        "pf": pf,
        "mean_pl": mean_pl,
        "sharpe": sharpe,
        "sortino": sortino,
        "maxdd": maxdd,
    }


def moving_block_bootstrap_sample(
    values: np.ndarray,
    block_size: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Resample a 1D series with replacement using moving blocks."""
    arr = np.asarray(values, dtype=float)
    n = arr.size
    if n == 0:
        return arr
    if n <= block_size:
        return rng.choice(arr, size=n, replace=True)

    starts = rng.integers(0, n - block_size + 1, size=int(np.ceil(n / block_size)))
    pieces = [arr[s:s + block_size] for s in starts]
    return np.concatenate(pieces)[:n]


def bootstrap_confidence_intervals(
    pnls: np.ndarray,
    n_boot: int = 1_000,
    ci: float = 0.90,
    method: str = "block",
    block_size: int = 5,
    seed: int = 42,
) -> dict[str, dict[str, float]]:
    """Bootstrap confidence intervals for trade metrics.

    method:
      - iid: standard bootstrap
      - block: moving block bootstrap that preserves short dependence
    """
    arr = np.asarray(pnls, dtype=float)
    if arr.size == 0:
        return {}

    rng = np.random.default_rng(seed)
    records = []
    for _ in range(n_boot):
        if method == "iid":
            sample = rng.choice(arr, size=arr.size, replace=True)
        else:
            sample = moving_block_bootstrap_sample(arr, block_size=max(1, block_size), rng=rng)
        records.append(compute_trade_metrics(sample))

    df = pd.DataFrame(records)
    lo_q = (1.0 - ci) / 2.0
    hi_q = 1.0 - lo_q
    result: dict[str, dict[str, float]] = {}
    for col in df.columns:
        result[col] = {
            "mean": float(df[col].mean()),
            "lo": float(df[col].quantile(lo_q)),
            "hi": float(df[col].quantile(hi_q)),
        }
    return result


def sign_flip_test(
    pnls: np.ndarray,
    n_perm: int = 10_000,
    seed: int = 42,
) -> dict[str, float]:
    """One-sample randomization test for mean trade P&L > 0.

    Under the null, trade signs are exchangeable and the observed mean could have
    arisen by random sign assignment. This is much more meaningful than permuting
    the same P&Ls against themselves, which leaves the mean unchanged.
    """
    arr = np.asarray(pnls, dtype=float)
    if arr.size == 0:
        return {
            "actual_mean_pl": 0.0,
            "null_mean": 0.0,
            "null_std": 0.0,
            "z_score": 0.0,
            "p_value": 1.0,
            "pct_simulations_beaten": 0.0,
        }

    rng = np.random.default_rng(seed)
    actual_mean = float(np.mean(arr))
    abs_arr = np.abs(arr)
    simulated = np.empty(n_perm, dtype=float)
    for i in range(n_perm):
        flips = rng.choice(np.array([-1.0, 1.0]), size=arr.size, replace=True)
        simulated[i] = float(np.mean(abs_arr * flips))

    null_mean = float(np.mean(simulated))
    null_std = float(np.std(simulated, ddof=1)) if simulated.size > 1 else 0.0
    p_value = float(np.mean(simulated >= actual_mean))
    z_score = (actual_mean - null_mean) / null_std if null_std > 1e-12 else 0.0
    pct_beaten = float(np.mean(simulated < actual_mean)) * 100.0

    return {
        "actual_mean_pl": actual_mean,
        "null_mean": null_mean,
        "null_std": null_std,
        "z_score": z_score,
        "p_value": p_value,
        "pct_simulations_beaten": pct_beaten,
    }


def forward_equity_simulation(
    pnls: np.ndarray,
    initial_balance: float = 10_000.0,
    months: int = 6,
    trades_per_month: int = 20,
    n_sim: int = 500,
    block_size: int = 5,
    seed: int = 42,
) -> dict[str, float]:
    """Monte Carlo forward equity paths using block-resampled trade returns."""
    returns = _normalized_trade_returns(np.asarray(pnls, dtype=float))
    if returns.size == 0:
        return {
            "months": float(months),
            "trades": float(months * trades_per_month),
            "median_return": 0.0,
            "p5_return": 0.0,
            "p25_return": 0.0,
            "p75_return": 0.0,
            "p95_return": 0.0,
            "prob_loss": 0.0,
            "prob_10pct": 0.0,
            "prob_ruin": 0.0,
        }

    rng = np.random.default_rng(seed)
    total_trades = months * trades_per_month
    final_equities = []
    for _ in range(n_sim):
        if returns.size >= block_size:
            sample = moving_block_bootstrap_sample(returns, block_size=max(1, block_size), rng=rng)
            if sample.size < total_trades:
                extra = moving_block_bootstrap_sample(returns, block_size=max(1, block_size), rng=rng)
                sample = np.concatenate([sample, extra])
            sample = sample[:total_trades]
        else:
            sample = rng.choice(returns, size=total_trades, replace=True)

        balance = initial_balance
        for ret in sample:
            balance *= (1.0 + float(ret))
            balance = max(balance, 0.0)
        final_equities.append(balance)

    eq = np.asarray(final_equities, dtype=float)
    ret_pct = (eq - initial_balance) / initial_balance * 100.0
    return {
        "months": float(months),
        "trades": float(total_trades),
        "median_return": float(np.median(ret_pct)),
        "p5_return": float(np.percentile(ret_pct, 5)),
        "p25_return": float(np.percentile(ret_pct, 25)),
        "p75_return": float(np.percentile(ret_pct, 75)),
        "p95_return": float(np.percentile(ret_pct, 95)),
        "prob_loss": float(np.mean(ret_pct < 0.0)) * 100.0,
        "prob_10pct": float(np.mean(ret_pct >= 10.0 * months)) * 100.0,
        "prob_ruin": float(np.mean(ret_pct <= -50.0)) * 100.0,
    }


def yearly_trade_summary(trades: pd.DataFrame) -> list[dict[str, float | str]]:
    """Summarize trade quality by calendar year."""
    if trades.empty:
        return []

    df = trades.copy()
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    rows: list[dict[str, float | str]] = []
    for year, group in df.groupby(df["entry_time"].dt.year):
        metrics = compute_trade_metrics(group["net_pl"].astype(float).values)
        rows.append({
            "year": str(year),
            "n_trades": int(metrics["n_trades"]),
            "wr": metrics["wr"],
            "pf": metrics["pf"],
            "mean_pl": metrics["mean_pl"],
            "maxdd": metrics["maxdd"],
        })
    return rows


def regime_trade_summary(
    trades: pd.DataFrame,
    labels: np.ndarray,
) -> dict[str, dict[str, float]]:
    """Summarize trade metrics by an arbitrary label array aligned to trades."""
    if trades.empty or len(trades) != len(labels):
        return {}

    buckets: dict[str, list[float]] = defaultdict(list)
    for pnl, label in zip(trades["net_pl"].astype(float).values, labels, strict=False):
        buckets[str(label)].append(float(pnl))

    result: dict[str, dict[str, float]] = {}
    for label, bucket in sorted(buckets.items()):
        result[label] = compute_trade_metrics(np.asarray(bucket, dtype=float))
    return result
