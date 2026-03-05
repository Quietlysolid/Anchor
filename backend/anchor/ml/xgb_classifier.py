"""
XGBoost direction classifier.

Predicts: LONG (1) or SHORT (0)
Returns: (confidence, direction)
"""
from __future__ import annotations

import numpy as np
import xgboost as xgb
import structlog

logger = structlog.get_logger(__name__)


class XGBClassifier:
    def __init__(self):
        self.model: xgb.XGBClassifier | None = None
        self._fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        """Train on feature matrix X and binary labels y (1=LONG, 0=SHORT)."""
        self.model = xgb.XGBClassifier(
            n_estimators=300,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=42,
            n_jobs=-1,
        )
        self.model.fit(X, y)
        self._fitted = True
        logger.info("xgb_trained", n_samples=len(X))

    def predict(self, features: np.ndarray) -> tuple[float, str]:
        """
        Returns (confidence: float, direction: str).
        """
        if not self._fitted or self.model is None:
            return 0.5, "LONG"

        x = features.reshape(1, -1)
        proba = self.model.predict_proba(x)[0]
        long_confidence  = float(proba[1])
        short_confidence = float(proba[0])

        if long_confidence >= short_confidence:
            return long_confidence, "LONG"
        else:
            return short_confidence, "SHORT"

    def save(self, path: str) -> None:
        if self.model:
            self.model.save_model(path)

    def load(self, path: str) -> None:
        self.model = xgb.XGBClassifier()
        self.model.load_model(path)
        self._fitted = True
