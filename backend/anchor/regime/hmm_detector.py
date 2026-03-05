"""
3-state Gaussian HMM for market regime detection.

States:
  RANGING   — low volatility, mean-reverting
  TRENDING  — sustained directional move
  VOLATILE  — high volatility, unpredictable

Features fed to HMM (daily resolution):
  - log returns
  - realized volatility (20-period std of log returns)
  - ATR-normalized price range
  - absolute trend strength (|close - SMA50| / SMA50)

State mapping stability fix:
  After every fit() or retrain, _assign_state_labels() locks the mapping
  by feature means. The mapping is also persisted to disk (pickle) so
  retraining on a new run produces consistent historical regime labels.
  This prevents the "state index remap" bug where RANGING and VOLATILE
  swap indices between training runs.
"""
from __future__ import annotations

import pickle
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import ta as ta_lib
import structlog
from hmmlearn import hmm

logger = structlog.get_logger(__name__)

DEFAULT_MODEL_PATH = Path("mlflow_artifacts/hmm_latest.pkl")


class HMMRegimeDetector:
    STATES = ["RANGING", "TRENDING", "VOLATILE"]

    def __init__(self, n_components: int = 3, n_iter: int = 1000):
        self.model = hmm.GaussianHMM(
            n_components=n_components,
            covariance_type="diag",   # more stable than 'full' with limited data
            n_iter=n_iter,
            random_state=42,
        )
        self._state_map: dict[int, str] = {}
        self._fitted = False

    def build_features(self, df_daily: pd.DataFrame) -> Tuple[np.ndarray, pd.Index]:
        """
        Build feature matrix from daily OHLCV.
        All features are computed without lookahead.
        """
        df = df_daily.copy()
        df["log_return"]   = np.log(df["close"] / df["close"].shift(1))
        df["realized_vol"] = df["log_return"].rolling(20).std() * np.sqrt(252)

        df["atr_norm"]     = (df["high"] - df["low"]) / df["close"].clip(lower=1e-10)

        sma50 = ta_lib.trend.SMAIndicator(close=df["close"], window=50).sma_indicator()
        df["trend_strength"] = ((df["close"] - sma50) / sma50.clip(lower=1e-10)).abs()

        features = df[["log_return", "realized_vol", "atr_norm", "trend_strength"]].dropna()
        return features.values.astype(np.float64), features.index

    def fit(self, df_daily: pd.DataFrame) -> None:
        """Train on daily OHLCV data."""
        features, _ = self.build_features(df_daily)
        if len(features) < 100:
            logger.warning("hmm_insufficient_data", rows=len(features))
            return

        self.model.fit(features)
        self._assign_state_labels()
        self._fitted = True
        logger.info("hmm_trained", n_samples=len(features), state_map=self._state_map)

    def predict_current(self, df_daily: pd.DataFrame) -> Tuple[str, float]:
        """
        Returns (regime_name: str, confidence: float).
        Uses last 60 days of features for prediction.
        """
        if not self._fitted:
            return "UNKNOWN", 0.0

        features, _ = self.build_features(df_daily)
        if len(features) < 10:
            return "UNKNOWN", 0.0

        recent = features[-60:]
        try:
            hidden_states = self.model.predict(recent)
            posteriors    = self.model.predict_proba(recent)
        except Exception as exc:
            logger.warning("hmm_predict_failed", error=str(exc))
            return "UNKNOWN", 0.0

        current_int = int(hidden_states[-1])
        confidence  = float(posteriors[-1, current_int])

        regime = self._state_map.get(current_int, "UNKNOWN")
        return regime, round(confidence, 4)

    def _assign_state_labels(self) -> None:
        """
        Assign semantic labels by inspecting state means.

        Stability guarantee: labels are assigned purely from feature means,
        independent of state index ordering. This ensures that retraining
        on new data does not cause state indices to "flip" (e.g., old state 0
        was RANGING but new state 0 is VOLATILE).

          - Highest realized_vol (feature idx 1) → VOLATILE
          - Highest trend_strength (feature idx 3) → TRENDING
          - Remaining state → RANGING
        """
        means = self.model.means_  # shape: (n_components, n_features)
        n = self.model.n_components

        # Sort by realized vol (feature 1) ascending → last index is highest vol
        vol_rank   = np.argsort(means[:, 1])
        # Sort by absolute trend strength (feature 3) ascending
        trend_rank = np.argsort(np.abs(means[:, 3]))

        self._state_map = {}
        volatile_idx = int(vol_rank[-1])
        self._state_map[volatile_idx] = "VOLATILE"

        # Highest trend strength that is NOT already labelled VOLATILE
        for idx in reversed(trend_rank.tolist()):
            if idx != volatile_idx:
                self._state_map[idx] = "TRENDING"
                break

        # All remaining states → RANGING
        for i in range(n):
            if i not in self._state_map:
                self._state_map[i] = "RANGING"

        logger.info("hmm_state_map_assigned", mapping=self._state_map)

    def save(self, path: Optional[Path] = None) -> None:
        """Persist model and state map together so labels survive restarts."""
        path = path or DEFAULT_MODEL_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"model": self.model, "state_map": self._state_map, "fitted": self._fitted}, f)
        logger.info("hmm_saved", path=str(path))

    def load(self, path: Optional[Path] = None) -> bool:
        """Load model and state map from disk."""
        path = path or DEFAULT_MODEL_PATH
        if not path.exists():
            logger.warning("hmm_model_not_found", path=str(path))
            return False
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.model     = data["model"]
        self._state_map = data["state_map"]
        self._fitted   = data.get("fitted", True)
        logger.info("hmm_loaded", path=str(path), state_map=self._state_map)
        return True

    @property
    def is_ready(self) -> bool:
        return self._fitted
