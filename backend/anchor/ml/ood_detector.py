"""
Out-of-Distribution (OOD) detector using Mahalanobis distance.

When live market conditions don't resemble the training distribution,
ML model predictions become unreliable. This detector flags such cases
so the system falls back to rule-based signals only.

Implementation note:
  EmpiricalCovariance.mahalanobis(x) returns the SQUARED Mahalanobis distance D².
  Under a multivariate normal distribution, D² ~ chi-squared(n_features).
  The threshold is therefore the chi-squared 99th percentile for n_features degrees
  of freedom, computed at fit time from the actual feature dimensionality.
  (Old code compared D² against threshold_std² * n_features which evaluates to
  ~153 for 17 features — far above the chi-squared 99.9th pct of ~40.8 — meaning
  the detector never fired.)
"""
from __future__ import annotations

import numpy as np
from scipy.stats import chi2
from sklearn.covariance import EmpiricalCovariance
import structlog

logger = structlog.get_logger(__name__)

# Confidence level for OOD threshold: flag samples beyond the 99th percentile
# of the training distribution (chi-squared with n_features degrees of freedom).
OOD_CONFIDENCE = 0.99


class OODDetector:
    def __init__(self, confidence: float = OOD_CONFIDENCE):
        self.confidence = confidence
        self._cov_estimator: EmpiricalCovariance | None = None
        self._threshold: float = float("inf")
        self._fitted = False

    def fit(self, X_train: np.ndarray) -> None:
        """Fit on training feature matrix."""
        self._cov_estimator = EmpiricalCovariance()
        self._cov_estimator.fit(X_train)
        # D² ~ chi2(n_features); set threshold at chosen confidence percentile.
        n_features = X_train.shape[1]
        self._threshold = float(chi2.ppf(self.confidence, df=n_features))
        self._fitted = True
        logger.info(
            "ood_detector_fitted",
            n_samples=len(X_train),
            n_features=n_features,
            threshold=round(self._threshold, 2),
            confidence=self.confidence,
        )

    def check(self, features: np.ndarray) -> bool:
        """
        Returns True if the sample is out-of-distribution.
        Compares squared Mahalanobis distance (D²) against chi-squared threshold.
        """
        if not self._fitted or self._cov_estimator is None:
            return False

        x = features.reshape(1, -1)
        try:
            # mahalanobis() returns D² (squared distance)
            d_squared = float(self._cov_estimator.mahalanobis(x)[0])
            is_ood = d_squared > self._threshold
            if is_ood:
                logger.warning(
                    "ood_detected",
                    d_squared=round(d_squared, 2),
                    threshold=round(self._threshold, 2),
                )
            return is_ood
        except Exception:
            return False
