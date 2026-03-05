"""
XGBoost direction classifier.

Predicts: LONG (1) or SHORT (0)
Returns: (confidence, direction)
"""
from __future__ import annotations

import pickle
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import xgboost as xgb
import structlog

logger = structlog.get_logger(__name__)

DEFAULT_MODEL_DIR = Path("/app/models")


class XGBDirectionClassifier:
    def __init__(self, model_path: Optional[Path] = None) -> None:
        self.model_path = model_path
        self._model: Optional[xgb.XGBClassifier] = None
        self._feature_names: list = []

    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: List[str] | None = None) -> None:
        """Train on feature matrix X and binary labels y (1=LONG, 0=SHORT)."""
        self._feature_names = feature_names or [f"f{i}" for i in range(X.shape[1])]
        self._model = xgb.XGBClassifier(
            n_estimators=300,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            random_state=42,
            n_jobs=-1,
        )
        self._model.fit(X, y)
        logger.info("xgb_trained", n_samples=len(X), features=len(self._feature_names))

    def predict(self, features: np.ndarray) -> Tuple[float, str]:
        """Returns (confidence: float, direction: str)."""
        if self._model is None:
            return 0.5, "LONG"

        x = features.reshape(1, -1)
        proba = self._model.predict_proba(x)[0]
        long_confidence  = float(proba[1])
        short_confidence = float(proba[0])

        if long_confidence >= short_confidence:
            return long_confidence, "LONG"
        else:
            return short_confidence, "SHORT"

    def save(self, path: Optional[Path] = None) -> None:
        path = path or self.model_path
        if path is None:
            raise ValueError("No path provided for save()")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"model": self._model, "feature_names": self._feature_names}, f)
        logger.info("xgb_saved", path=str(path))

    def load(self, path: Optional[Path] = None) -> bool:
        path = path or self.model_path
        if path is None:
            return False
        path = Path(path)
        if not path.exists():
            logger.warning("xgb_model_not_found", path=str(path))
            return False
        with open(path, "rb") as f:
            data = pickle.load(f)
        self._model = data["model"]
        self._feature_names = data.get("feature_names", [])
        logger.info("xgb_loaded", path=str(path))
        return True

    @property
    def is_ready(self) -> bool:
        return self._model is not None


# Backwards-compat alias
XGBClassifier = XGBDirectionClassifier
