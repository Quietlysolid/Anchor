"""
Out-of-Distribution (OOD) detector using Mahalanobis distance.

When live market conditions don't resemble the training distribution,
ML model predictions become unreliable. This detector flags such cases
so the system falls back to rule-based signals only.
"""
from __future__ import annotations

import numpy as np
from sklearn.covariance import EmpiricalCovariance
import structlog

logger = structlog.get_logger(__name__)

OOD_THRESHOLD_STD = 3.0


class OODDetector:
    def __init__(self, threshold_std: float = OOD_THRESHOLD_STD):
        self.threshold = threshold_std
        self._cov_estimator: EmpiricalCovariance | None = None
        self._mean: np.ndarray | None = None
        self._fitted = False

    def fit(self, X_train: np.ndarray) -> None:
        """Fit on training feature matrix."""
        self._mean = X_train.mean(axis=0)
        self._cov_estimator = EmpiricalCovariance()
        self._cov_estimator.fit(X_train)
        self._fitted = True
        logger.info("ood_detector_fitted", n_samples=len(X_train))

    def check(self, features: np.ndarray) -> bool:
        """
        Returns True if the sample is out-of-distribution.
        Uses Mahalanobis distance from training distribution.
        """
        if not self._fitted or self._cov_estimator is None:
            return False

        x = features.reshape(1, -1)
        try:
            dist = float(self._cov_estimator.mahalanobis(x)[0])
            # Convert to approximate z-score (chi2 distribution)
            # For rough OOD threshold: if Mahalanobis > threshold^2 * n_features
            n_features = features.shape[0]
            threshold = self.threshold ** 2 * n_features

            is_ood = dist > threshold
            if is_ood:
                logger.warning("ood_detected", mahalanobis=dist, threshold=threshold)
            return is_ood
        except Exception:
            return False
