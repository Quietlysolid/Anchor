"""
ATR-based market regime classifier for backtesting.

Drop-in replacement for HMMRegimeDetector when no trained HMM model is
available (e.g. new pairs without 2+ years of live history).

Same interface as HMMRegimeDetector: is_ready, predict_current().

Classification rules (calibrated to typical HMM state boundaries in FX):
  VOLATILE  — recent realized vol > 130% of rolling baseline (elevated uncertainty)
  TRENDING  — price displaced > 2.5 ATRs from SMA50 (sustained directional move)
  RANGING   — everything else (low vol, mean-reverting, no clear displacement)

These thresholds correspond to the feature means the HMM learns internally
when trained on FX daily data:
  - VOLATILE state: realized_vol mean ≈ 1.3–1.5× the TRENDING/RANGING baseline
  - TRENDING state: trend_strength (|close-SMA50|/close) ≈ 0.5–1.5% displacement
  - RANGING state:  trend_strength < 0.3%, vol below baseline
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class AtrRegimeClassifier:
    """Rule-based regime classifier derived from ATR and volatility features.

    No training required — valid from the very first bar of backtest history.
    Confidence is a heuristic measure of how far each metric is from its threshold.
    """

    @property
    def is_ready(self) -> bool:
        return True

    def predict_current(self, df_daily: pd.DataFrame) -> tuple[str, float]:
        """Return (regime_name, confidence) from daily OHLCV data.

        Uses up to the last 150 bars as context; requires at least 60.
        Fails open to UNKNOWN when insufficient data.
        """
        if df_daily is None or len(df_daily) < 60:
            return "UNKNOWN", 0.0

        df = df_daily.copy().tail(150)

        # Realized volatility: 20-day rolling std of log returns, annualized
        df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
        df["rv20"] = df["log_ret"].rolling(20).std() * np.sqrt(252)

        # ATR (14-period simple)
        prev_close = df["close"].shift(1)
        df["tr"] = np.maximum(
            df["high"] - df["low"],
            np.maximum(
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ),
        )
        df["atr14"] = df["tr"].rolling(14).mean()

        # Trend displacement: |close - SMA50| in units of ATR
        df["sma50"] = df["close"].rolling(50).mean()
        df["displacement"] = (df["close"] - df["sma50"]).abs() / df["atr14"].clip(lower=1e-10)

        df = df.dropna()
        if len(df) < 30:
            return "UNKNOWN", 0.0

        current_rv = float(df["rv20"].iloc[-1])
        current_disp = float(df["displacement"].iloc[-1])

        # Historical vol baseline: 75th percentile of realized vol in window
        # (higher than median to avoid false VOLATILE labels in quiet periods)
        vol_baseline = float(df["rv20"].quantile(0.75))
        if vol_baseline < 1e-8:
            return "UNKNOWN", 0.0

        vol_ratio = current_rv / vol_baseline

        # ── VOLATILE: recent vol significantly above rolling baseline ─────
        if vol_ratio > 1.3:
            # Confidence scales from 0.55 at threshold to ~1.0 at 2× baseline
            conf = min(1.0, 0.55 + (vol_ratio - 1.3) / 1.4)
            return "VOLATILE", round(conf, 4)

        # ── TRENDING: price displaced > 2.5 ATRs from SMA50 ─────────────
        if current_disp > 2.5:
            # Confidence scales from 0.60 at threshold to ~0.95 at 5× ATR
            conf = min(0.95, 0.60 + (current_disp - 2.5) / 8.0)
            return "TRENDING", round(conf, 4)

        # ── RANGING: low vol, no significant trend displacement ───────────
        # Confidence: inversely proportional to how close we are to TRENDING
        conf = min(0.85, 0.55 + max(0.0, 2.5 - current_disp) / 5.0)
        return "RANGING", round(conf, 4)
