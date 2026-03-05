"""
Currency Strength Index (CSI).

For each of 8 currencies, computes normalized average return
vs all 7 other currencies over a rolling period.

Result: normalized score 0-100 per currency.
Signal: trade the strong vs the weak.
"""
from collections import defaultdict

import numpy as np
import pandas as pd


CURRENCIES = ["EUR", "GBP", "USD", "JPY", "CHF", "AUD", "NZD", "CAD"]

# Which instrument to use for each currency pair
PAIR_MAP: dict[tuple[str, str], str] = {
    ("EUR", "USD"): "EUR_USD", ("EUR", "GBP"): "EUR_GBP",
    ("EUR", "JPY"): "EUR_JPY", ("EUR", "CHF"): "EUR_CHF",
    ("EUR", "AUD"): "EUR_AUD", ("EUR", "NZD"): "EUR_NZD",
    ("EUR", "CAD"): "EUR_CAD", ("GBP", "USD"): "GBP_USD",
    ("GBP", "JPY"): "GBP_JPY", ("GBP", "CHF"): "GBP_CHF",
    ("GBP", "AUD"): "GBP_AUD", ("GBP", "NZD"): "GBP_NZD",
    ("GBP", "CAD"): "GBP_CAD", ("USD", "JPY"): "USD_JPY",
    ("USD", "CHF"): "USD_CHF", ("USD", "CAD"): "USD_CAD",
    ("AUD", "USD"): "AUD_USD", ("NZD", "USD"): "NZD_USD",
    ("AUD", "JPY"): "AUD_JPY", ("NZD", "JPY"): "NZD_JPY",
    ("CHF", "JPY"): "CHF_JPY", ("CAD", "JPY"): "CAD_JPY",
    ("AUD", "CAD"): "AUD_CAD", ("AUD", "NZD"): "AUD_NZD",
    ("NZD", "CAD"): "NZD_CAD", ("AUD", "CHF"): "AUD_CHF",
    ("NZD", "CHF"): "NZD_CHF", ("CAD", "CHF"): "CAD_CHF",
}


def compute_csi(
    candle_cache: dict[str, pd.DataFrame],
    period: int = 14,
) -> dict[str, float]:
    """
    Returns normalized strength score 0-100 per currency.
    candle_cache: instrument → DataFrame with 'close' column.
    """
    strengths: dict[str, list[float]] = defaultdict(list)

    for (base, quote), instrument in PAIR_MAP.items():
        df = candle_cache.get(instrument)
        if df is None or len(df) < period + 1:
            continue

        closes = df["close"]
        period_return = (float(closes.iloc[-1]) - float(closes.iloc[-period])) / float(closes.iloc[-period])

        strengths[base].append(period_return)
        strengths[quote].append(-period_return)  # reverse for quote currency

    if not strengths:
        return {c: 50.0 for c in CURRENCIES}

    raw = {ccy: float(np.mean(rets)) for ccy, rets in strengths.items()}

    # Normalize to 0-100
    values = np.array(list(raw.values()))
    mn, mx = values.min(), values.max()
    spread = mx - mn

    if spread < 1e-10:
        return {ccy: 50.0 for ccy in CURRENCIES}

    return {
        ccy: round(((raw.get(ccy, 0) - mn) / spread) * 100, 2)
        for ccy in CURRENCIES
    }


def csi_signal_score(
    instrument: str,
    csi: dict[str, float],
    direction: str,
) -> float:
    """
    Score 0.0-1.0 for how well the CSI supports the direction.
    LONG: base should be stronger than quote.
    SHORT: base should be weaker than quote.
    """
    parts = instrument.split("_")
    if len(parts) != 2:
        return 0.5

    base, quote = parts
    base_strength  = csi.get(base, 50.0)
    quote_strength = csi.get(quote, 50.0)
    diff = abs(base_strength - quote_strength)
    normalized_diff = diff / 100.0

    if direction == "LONG":
        return normalized_diff if base_strength > quote_strength else 0.0
    else:
        return normalized_diff if quote_strength > base_strength else 0.0
