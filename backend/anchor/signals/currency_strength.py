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

    Normalization: z-score across currencies, then mapped to [0, 100] via a
    sigmoid (tanh-based) so a single extreme outlier (e.g. JPY during FOMC)
    does not compress all other currency differences toward a narrow band.

    Averaging: the mean return per currency is weighted by observation count
    to avoid giving equal weight to currencies with only 1 pair vs those with
    5 pairs (USD). Currencies absent from the loaded pairs are not imputed —
    they receive 50.0 (neutral) rather than being pulled to 0 by the old
    min-max formula.
    """
    # Accumulate returns and observation counts per currency
    sum_rets: dict[str, float] = defaultdict(float)
    obs_count: dict[str, int] = defaultdict(int)

    for (base, quote), instrument in PAIR_MAP.items():
        df = candle_cache.get(instrument)
        if df is None or len(df) < period + 1:
            continue

        closes = df["close"]
        period_return = (float(closes.iloc[-1]) - float(closes.iloc[-period])) / float(closes.iloc[-period])

        sum_rets[base] += period_return
        obs_count[base] += 1
        sum_rets[quote] += -period_return
        obs_count[quote] += 1

    observed = {ccy for ccy in CURRENCIES if obs_count.get(ccy, 0) > 0}
    if not observed:
        return {c: 50.0 for c in CURRENCIES}

    # Weighted mean (= sum / count, naturally handles uneven coverage)
    raw = {ccy: sum_rets[ccy] / obs_count[ccy] for ccy in observed}

    # Z-score across observed currencies
    values = np.array(list(raw.values()))
    mu  = float(values.mean())
    std = float(values.std(ddof=0))

    result: dict[str, float] = {}
    for ccy in CURRENCIES:
        if ccy not in raw:
            result[ccy] = 50.0  # neutral for unobserved currencies
            continue
        if std < 1e-10:
            result[ccy] = 50.0
        else:
            z = (raw[ccy] - mu) / std
            # tanh maps z-scores to (-1, 1); scale to (0, 100)
            # tanh saturates at ~±3σ, so outliers beyond 3σ don't
            # compress other currencies — they just hit ~97 or ~3.
            result[ccy] = round(50.0 + 50.0 * float(np.tanh(z)), 2)

    return result


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
