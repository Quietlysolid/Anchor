import numpy as np


def wilder_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    atr = np.full(n, np.nan)
    if n > period:
        atr[period] = float(np.mean(tr[1 : period + 1]))
        alpha = 1.0 / period
        for i in range(period + 1, n):
            atr[i] = alpha * tr[i] + (1.0 - alpha) * atr[i - 1]
    return atr


def wilder_atr_scalar(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    prev_closes = np.roll(closes, 1)
    prev_closes[0] = closes[0]
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_closes), np.abs(lows - prev_closes)))
    atr = float(np.mean(tr[:period]))
    alpha = 1.0 / period
    for tv in tr[period:]:
        atr = alpha * float(tv) + (1.0 - alpha) * atr
    return atr


PRICE_INCREMENTS: dict[str, float] = {
    "ES": 0.25,
    "MES": 0.25,
    "NQ": 0.25,
    "MNQ": 0.25,
    "YM": 1.0,
    "MYM": 1.0,
    "RTY": 0.1,
    "M2K": 0.1,
    "CL": 0.01,
    "MCL": 0.01,
    "GC": 0.1,
    "MGC": 0.1,
}


def get_pip_size(instrument: str) -> float:
    return PRICE_INCREMENTS.get(instrument.upper(), 0.25)


def pips_to_price(instrument: str, pips: float) -> float:
    return pips * get_pip_size(instrument)


def price_to_pips(instrument: str, price_diff: float) -> float:
    return abs(price_diff) / get_pip_size(instrument)


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
