import numpy as np


def wilder_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder's smoothed ATR as a full array. matches the `ta` library used in live trading.

    Returns an array of length len(closes) where the first `period` values are NaN.
    Use float(result[-1]) to get the current ATR value.
    """
    n = len(closes)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
    atr = np.full(n, np.nan)
    if n > period:
        atr[period] = float(np.mean(tr[1: period + 1]))
        alpha = 1.0 / period
        for i in range(period + 1, n):
            atr[i] = alpha * tr[i] + (1.0 - alpha) * atr[i - 1]
    return atr


def wilder_atr_scalar(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    """Returns the single current ATR value (last element of wilder_atr)."""
    prev_closes = np.roll(closes, 1)
    prev_closes[0] = closes[0]
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_closes), np.abs(lows - prev_closes)))
    atr = float(np.mean(tr[:period]))
    alpha = 1.0 / period
    for tv in tr[period:]:
        atr = alpha * float(tv) + (1.0 - alpha) * atr
    return atr


PIP_SIZES: dict[str, float] = {
    "EUR_USD": 0.0001, "GBP_USD": 0.0001, "AUD_USD": 0.0001,
    "NZD_USD": 0.0001, "USD_CAD": 0.0001, "USD_CHF": 0.0001,
    "EUR_GBP": 0.0001, "EUR_CAD": 0.0001, "GBP_CAD": 0.0001,
    "USD_JPY": 0.01,   "EUR_JPY": 0.01,   "GBP_JPY": 0.01,
    "AUD_JPY": 0.01,   "CHF_JPY": 0.01,   "CAD_JPY": 0.01,
    "NZD_JPY": 0.01,
}


def get_pip_size(instrument: str) -> float:
    return PIP_SIZES.get(instrument, 0.0001)


def pips_to_price(instrument: str, pips: float) -> float:
    return pips * get_pip_size(instrument)


def price_to_pips(instrument: str, price_diff: float) -> float:
    pip = get_pip_size(instrument)
    return abs(price_diff) / pip


def sharpe_ratio(returns: np.ndarray, risk_free: float = 0.0, periods: int = 252) -> float:
    excess = returns - risk_free / periods
    std = np.std(excess, ddof=1)
    effective_std = max(float(std), 1e-12)
    return float(np.mean(excess) / effective_std * np.sqrt(periods))


def sortino_ratio(returns: np.ndarray, risk_free: float = 0.0, periods: int = 252) -> float:
    excess = returns - risk_free / periods
    downside = excess[excess < 0]
    downside_std = np.std(downside, ddof=1) if len(downside) > 1 else 0.0
    if np.isclose(downside_std, 0.0, atol=1e-12):
        return 0.0
    return float(np.mean(excess) / downside_std * np.sqrt(periods))


def max_drawdown(equity_curve: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity_curve)
    drawdown = (equity_curve - peak) / peak
    return float(np.min(drawdown))


def calmar_ratio(returns: np.ndarray, periods: int = 252) -> float:
    cagr = float(np.mean(returns) * periods)
    equity = np.cumprod(1 + returns)
    mdd = abs(max_drawdown(equity))
    if mdd == 0:
        return 0.0
    return cagr / mdd


def profit_factor(pnl_series: np.ndarray) -> float:
    gross_profit = np.sum(pnl_series[pnl_series > 0])
    gross_loss = abs(np.sum(pnl_series[pnl_series < 0]))
    if gross_loss == 0:
        return float("inf")
    return float(gross_profit / gross_loss)
