"""LightGBM binary classifier for trade direction prediction.

Mirrors the XGBoost classifier interface so either can be swapped in.
"""
from __future__ import annotations

import pickle
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import structlog
from lightgbm import LGBMClassifier

logger = structlog.get_logger(__name__)

DEFAULT_MODEL_PATH = Path("mlflow_artifacts/lgbm_latest.pkl")


class LGBMDirectionClassifier:
    def __init__(self, model_path: Optional[Path] = None) -> None:
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self._model: Optional[LGBMClassifier] = None
        self._feature_names: list = []

    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: list) -> None:
        self._feature_names = feature_names
        self._model = LGBMClassifier(
            n_estimators=400,
            max_depth=6,
            learning_rate=0.03,
            num_leaves=31,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_samples=20,
            class_weight="balanced",
            random_state=42,
            verbose=-1,
        )
        self._model.fit(X, y)
        logger.info("lgbm_trained", samples=len(X), features=len(feature_names))

    def predict(self, features: np.ndarray) -> Tuple[float, str]:
        """Return (confidence, direction) where confidence ∈ [0, 1]."""
        if self._model is None:
            raise RuntimeError("LGBMClassifier not fitted or loaded")

        x = features.reshape(1, -1)
        proba = self._model.predict_proba(x)[0]
        # Class 0 = SHORT, class 1 = LONG
        long_prob = float(proba[1])
        short_prob = float(proba[0])

        if long_prob >= short_prob:
            return long_prob, "LONG"
        return short_prob, "SHORT"

    def save(self, path: Optional[Path] = None) -> None:
        path = path or self.model_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"model": self._model, "feature_names": self._feature_names}, f)
        logger.info("lgbm_saved", path=str(path))

    def load(self, path: Optional[Path] = None) -> bool:
        path = path or self.model_path
        if not path.exists():
            logger.warning("lgbm_model_not_found", path=str(path))
            return False
        with open(path, "rb") as f:
            data = pickle.load(f)
        self._model = data["model"]
        self._feature_names = data.get("feature_names", [])
        logger.info("lgbm_loaded", path=str(path))
        return True

    @property
    def is_ready(self) -> bool:
        return self._model is not None
