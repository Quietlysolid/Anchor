"""
Feature engineering for XGBoost/LightGBM.

All features use .shift(1) or look only at bar[-1] computed from
data available at the time of signal generation.
NO lookahead bias.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import ta as ta_lib


class FeatureEngineer:
    """
    Builds a 1D feature vector for a given instrument + OHLCV data.
    """

    def build(
        self,
        instrument: str,
        df_1h:  pd.DataFrame,
        df_4h:  pd.DataFrame | None = None,
    ) -> np.ndarray:
        """
        Returns 1D numpy array of features.
        Uses only data available at last closed bar (no lookahead).
        """
        features = {}

        # ── Momentum ──────────────────────────────────────────────────────
        rsi_1h = ta_lib.momentum.RSIIndicator(close=df_1h["close"], window=14).rsi()
        if rsi_1h is not None and not rsi_1h.isna().all():
            features["rsi_1h"]      = float(rsi_1h.iloc[-2])
            features["rsi_1h_prev"] = float(rsi_1h.iloc[-3])

        macd_signal = ta_lib.trend.MACD(close=df_1h["close"]).macd_signal()
        if macd_signal is not None and not macd_signal.isna().all():
            features["macd_signal"] = float(macd_signal.iloc[-2])

        stoch_k = ta_lib.momentum.StochasticOscillator(
            high=df_1h["high"], low=df_1h["low"], close=df_1h["close"]
        ).stoch()
        if stoch_k is not None and not stoch_k.isna().all():
            features["stoch_k"] = float(stoch_k.iloc[-2])

        # ── Volatility ────────────────────────────────────────────────────
        atr_series = ta_lib.volatility.AverageTrueRange(
            high=df_1h["high"], low=df_1h["low"], close=df_1h["close"], window=14
        ).average_true_range()
        atr_val = 0.0
        if atr_series is not None and not atr_series.isna().all():
            atr_val   = float(atr_series.iloc[-2])
            close_val = float(df_1h["close"].iloc[-2])
            features["atr_norm"] = atr_val / close_val if close_val else 0.0

        bb_obj = ta_lib.volatility.BollingerBands(close=df_1h["close"], window=20, window_dev=2.0)
        bb_upper = bb_obj.bollinger_hband()
        bb_lower = bb_obj.bollinger_lband()
        bb_mid   = bb_obj.bollinger_mavg()
        if all(s is not None and not s.isna().all() for s in [bb_upper, bb_lower, bb_mid]):
            upper = float(bb_upper.iloc[-2])
            lower = float(bb_lower.iloc[-2])
            mid   = float(bb_mid.iloc[-2])
            features["bb_width"] = (upper - lower) / mid if mid else 0.0

        log_returns = np.log(df_1h["close"] / df_1h["close"].shift(1)).dropna()
        if len(log_returns) >= 20:
            features["realized_vol"] = float(log_returns.iloc[-20:].std() * np.sqrt(24 * 252))

        # ── Trend ─────────────────────────────────────────────────────────
        adx_series = ta_lib.trend.ADXIndicator(
            high=df_1h["high"], low=df_1h["low"], close=df_1h["close"], window=14
        ).adx()
        if adx_series is not None and not adx_series.isna().all():
            features["adx_1h"] = float(adx_series.iloc[-2])

        ema50 = ta_lib.trend.EMAIndicator(close=df_1h["close"], window=50).ema_indicator()
        if ema50 is not None and not ema50.isna().all():
            ema_val   = float(ema50.iloc[-2])
            close_val = float(df_1h["close"].iloc[-2])
            features["dist_from_ema50"] = (close_val - ema_val) / (atr_val if atr_val else 1.0)

        # 4H features
        if df_4h is not None and len(df_4h) >= 55:
            adx_4h = ta_lib.trend.ADXIndicator(
                high=df_4h["high"], low=df_4h["low"], close=df_4h["close"], window=14
            ).adx()
            if adx_4h is not None and not adx_4h.isna().all():
                features["adx_4h"] = float(adx_4h.iloc[-2])

        # ── Time encoding (cyclic, no ordinal leakage) ────────────────────
        last_ts = df_1h.index[-1]
        hour = last_ts.hour
        dow  = last_ts.dayofweek
        features["hour_sin"] = float(np.sin(2 * np.pi * hour / 24))
        features["hour_cos"] = float(np.cos(2 * np.pi * hour / 24))
        features["dow_sin"]  = float(np.sin(2 * np.pi * dow / 7))
        features["dow_cos"]  = float(np.cos(2 * np.pi * dow / 7))

        # Session binary flags
        features["is_london"]   = 1.0 if 7  <= hour < 17 else 0.0
        features["is_newyork"]  = 1.0 if 12 <= hour < 21 else 0.0
        features["is_asian"]    = 1.0 if hour < 9          else 0.0

        # ── Fill any missing features with 0.0 ───────────────────────────
        return np.array(
            [features.get(k, 0.0) for k in sorted(features.keys())],
            dtype=np.float32,
        )

    def compute(self, df: pd.DataFrame, instrument: str) -> np.ndarray:
        """Alias for build() used by retraining pipeline. df is H1 window."""
        return self.build(instrument, df)

    def get_feature_names(self) -> list[str]:
        """Returns sorted list of feature names (must match build() output)."""
        names = [
            "adx_1h", "adx_4h", "atr_norm", "bb_width",
            "dist_from_ema50", "dow_cos", "dow_sin",
            "hour_cos", "hour_sin", "is_asian", "is_london", "is_newyork",
            "macd_signal", "realized_vol",
            "rsi_1h", "rsi_1h_prev", "stoch_k",
        ]
        return sorted(names)

    @property
    def feature_names(self) -> list[str]:
        return self.get_feature_names()
