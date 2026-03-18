"""
XGBoost direction classifier.

Predicts: LONG (1) or SHORT (0)
Returns: (confidence, direction)
"""
from __future__ import annotations

import os
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import xgboost as xgb
import structlog

logger = structlog.get_logger(__name__)

DEFAULT_MODEL_DIR = Path("/app/models")


class XGBDirectionClassifier:
    _STALE_DAYS = 30

    def __init__(self, model_path: Optional[Path] = None) -> None:
        self.model_path = model_path
        self._model: Optional[xgb.XGBClassifier] = None
        self._feature_names: list = []
        self._model_mtime: Optional[datetime] = None

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: List[str] | None = None,
        class_weight: str | None = "balanced",
    ) -> None:
        """Train on feature matrix X and binary labels y (1=LONG, 0=SHORT).

        class_weight="balanced" computes scale_pos_weight = n_neg / n_pos so
        XGBoost handles class-imbalanced label distributions the same way
        LightGBM does with class_weight="balanced".
        """
        self._feature_names = feature_names or [f"f{i}" for i in range(X.shape[1])]

        scale_pos_weight = 1.0
        if class_weight == "balanced":
            n_pos = float(np.sum(y == 1))
            n_neg = float(np.sum(y == 0))
            if n_pos > 0:
                scale_pos_weight = n_neg / n_pos

        self._model = xgb.XGBClassifier(
            n_estimators=300,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            scale_pos_weight=scale_pos_weight,
            random_state=42,
            n_jobs=-1,
        )
        self._model.fit(X, y)
        logger.info(
            "xgb_trained",
            n_samples=len(X),
            features=len(self._feature_names),
            scale_pos_weight=round(scale_pos_weight, 4),
        )

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
        self._model_mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
        logger.info("xgb_loaded", path=str(path), mtime=self._model_mtime.isoformat())
        return True

    @property
    def is_stale(self) -> bool:
        """True if model file is older than _STALE_DAYS or mtime is unknown."""
        if self._model_mtime is None:
            return True
        return (datetime.now(timezone.utc) - self._model_mtime).days > self._STALE_DAYS

    @property
    def is_ready(self) -> bool:
        return self._model is not None


# Backwards-compat alias
XGBClassifier = XGBDirectionClassifier
