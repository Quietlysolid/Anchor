"""Walk-forward cross-validation for ML classifiers.

Uses expanding window: train on N months, test on next M months,
roll forward. Prevents temporal data leakage.

Returns per-fold accuracy, final out-of-sample (OOS) accuracy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np
import structlog
from sklearn.metrics import accuracy_score, classification_report

from anchor.ml.xgb_classifier import XGBDirectionClassifier
from anchor.ml.lgbm_classifier import LGBMDirectionClassifier

logger = structlog.get_logger(__name__)

TRAIN_WINDOW_MONTHS = 12  # initial training window
TEST_WINDOW_MONTHS = 1    # out-of-sample test window
MIN_TRAIN_SAMPLES = 200   # skip fold if too few samples


@dataclass
class FoldResult:
    fold: int
    train_samples: int
    test_samples: int
    accuracy: float
    report: str


@dataclass
class WalkForwardResult:
    folds: List[FoldResult] = field(default_factory=list)
    oos_accuracy: float = 0.0
    oos_samples: int = 0


def walk_forward_validate(
    X: np.ndarray,
    y: np.ndarray,
    timestamps: np.ndarray,  # UTC datetime64 array aligned with X rows
    model_type: str = "xgb",
    feature_names: List[str] | None = None,
) -> WalkForwardResult:
    """Run walk-forward validation and return per-fold results."""
    result = WalkForwardResult()
    feature_names = feature_names or [f"f{i}" for i in range(X.shape[1])]

    # Convert timestamps to month indices for windowing
    ts = timestamps.astype("datetime64[M]")
    unique_months = np.unique(ts)
    n_months = len(unique_months)

    if n_months < TRAIN_WINDOW_MONTHS + TEST_WINDOW_MONTHS:
        logger.warning("walk_forward_insufficient_months", n_months=n_months)
        return result

    all_test_preds: List[int] = []
    all_test_true: List[int] = []

    fold = 0
    train_end_idx = TRAIN_WINDOW_MONTHS
    while train_end_idx + TEST_WINDOW_MONTHS <= n_months:
        train_months = unique_months[:train_end_idx]
        test_months = unique_months[train_end_idx: train_end_idx + TEST_WINDOW_MONTHS]

        train_mask = np.isin(ts, train_months)
        test_mask = np.isin(ts, test_months)

        X_train, y_train = X[train_mask], y[train_mask]
        X_test, y_test = X[test_mask], y[test_mask]

        if len(X_train) < MIN_TRAIN_SAMPLES or len(X_test) == 0:
            train_end_idx += 1
            continue

        # Fit model
        if model_type == "lgbm":
            clf = LGBMDirectionClassifier()
        else:
            clf = XGBDirectionClassifier()

        clf.fit(X_train, y_train, feature_names)

        # Predict
        preds = []
        for row in X_test:
            conf, direction = clf.predict(row)
            preds.append(1 if direction == "LONG" else 0)

        acc = accuracy_score(y_test, preds)
        report = classification_report(y_test, preds, target_names=["SHORT", "LONG"], zero_division=0)

        fold_result = FoldResult(
            fold=fold,
            train_samples=len(X_train),
            test_samples=len(X_test),
            accuracy=round(acc, 4),
            report=report,
        )
        result.folds.append(fold_result)
        all_test_preds.extend(preds)
        all_test_true.extend(y_test.tolist())

        logger.info(
            "walk_forward_fold",
            fold=fold,
            train=len(X_train),
            test=len(X_test),
            accuracy=round(acc * 100, 1),
        )

        fold += 1
        train_end_idx += 1  # expanding window

    if all_test_true:
        result.oos_accuracy = round(accuracy_score(all_test_true, all_test_preds), 4)
        result.oos_samples = len(all_test_true)
        logger.info(
            "walk_forward_complete",
            folds=len(result.folds),
            oos_accuracy=round(result.oos_accuracy * 100, 1),
            oos_samples=result.oos_samples,
        )

    return result
