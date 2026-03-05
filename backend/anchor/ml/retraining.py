"""Monthly ML retraining pipeline.

Called by Celery task on the 1st of each month at 06:00 UTC.
1. Fetch 2 years of candle data per instrument from DB
2. Engineer features (strict no-lookahead)
3. Run walk-forward validation
4. Train final model on full dataset
5. Register in MLflow with metrics
6. Promote to production if OOS accuracy >= threshold
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import mlflow
import numpy as np
import structlog

from anchor.config import settings
from anchor.database.engine import get_session
from anchor.database.repositories import MarketDataRepository
from anchor.ml.feature_engineer import FeatureEngineer
from anchor.ml.walk_forward import walk_forward_validate, WalkForwardResult
from anchor.ml.xgb_classifier import XGBDirectionClassifier
from anchor.ml.lgbm_classifier import LGBMDirectionClassifier
from anchor.ml.model_registry import ModelRegistry

logger = structlog.get_logger(__name__)

LOOKBACK_YEARS = 2
MIN_OOS_ACCURACY = 0.58  # must beat this to replace production model
LABEL_FORWARD_BARS = 3   # label = price direction 3 bars ahead


def _make_labels(closes: np.ndarray, forward_bars: int = LABEL_FORWARD_BARS) -> np.ndarray:
    """Binary label: 1 if price rose N bars later, 0 if fell."""
    future = np.roll(closes, -forward_bars)
    labels = (future > closes).astype(int)
    labels[-forward_bars:] = -1  # invalid (lookahead)
    return labels


async def run_retraining(instrument: Optional[str] = None) -> Dict:
    """Run full retraining pipeline. Returns results summary."""
    instruments = [instrument] if instrument else settings.instruments
    results = {}

    async with get_session() as session:
        repo = MarketDataRepository(session)
        engineer = FeatureEngineer()
        registry = ModelRegistry()

        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        mlflow.set_experiment("anchor_retraining")

        for instr in instruments:
            logger.info("retraining_start", instrument=instr)

            # Fetch 2 years of H1 candles
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=365 * LOOKBACK_YEARS)
            candles = await repo.get_candles(instr, "H1", start, end, limit=50000)

            if len(candles) < 500:
                logger.warning("retraining_insufficient_data", instrument=instr, candles=len(candles))
                continue

            # Build feature matrix
            import pandas as pd
            df = pd.DataFrame(
                {
                    "time": [c.time for c in candles],
                    "open": [c.open for c in candles],
                    "high": [c.high for c in candles],
                    "low": [c.low for c in candles],
                    "close": [c.close for c in candles],
                    "volume": [c.volume or 0 for c in candles],
                }
            )
            df = df.set_index("time").sort_index()

            feature_rows = []
            timestamps = []
            for i in range(60, len(df)):
                window = df.iloc[i - 60: i]
                try:
                    feat = engineer.compute(window, instr)
                    feature_rows.append(feat)
                    timestamps.append(df.index[i])
                except Exception:
                    continue

            if not feature_rows:
                continue

            X = np.array(feature_rows, dtype=np.float32)
            closes = df["close"].values[60: 60 + len(feature_rows)]
            labels = _make_labels(closes)
            valid_mask = labels >= 0
            X = X[valid_mask]
            y = labels[valid_mask]
            ts = np.array(timestamps)[valid_mask].astype("datetime64[M]")

            feature_names = engineer.get_feature_names()

            with mlflow.start_run(run_name=f"{instr}_{datetime.now().strftime('%Y%m')}"):
                # Walk-forward
                wf_result: WalkForwardResult = walk_forward_validate(X, y, ts, "xgb", feature_names)
                mlflow.log_metric("oos_accuracy", wf_result.oos_accuracy)
                mlflow.log_metric("oos_samples", wf_result.oos_samples)
                mlflow.log_metric("n_folds", len(wf_result.folds))
                mlflow.log_param("instrument", instr)
                mlflow.log_param("train_samples", len(X))

                if wf_result.oos_accuracy >= MIN_OOS_ACCURACY:
                    # Train final model on all data
                    clf = XGBDirectionClassifier()
                    clf.fit(X, y, feature_names)

                    # Log and register
                    run_id = mlflow.active_run().info.run_id
                    clf.save()
                    await registry.promote(instr, run_id, wf_result.oos_accuracy)
                    logger.info(
                        "retraining_promoted",
                        instrument=instr,
                        oos_accuracy=wf_result.oos_accuracy,
                    )
                    results[instr] = {"status": "promoted", "oos_accuracy": wf_result.oos_accuracy}
                else:
                    logger.warning(
                        "retraining_below_threshold",
                        instrument=instr,
                        oos_accuracy=wf_result.oos_accuracy,
                        threshold=MIN_OOS_ACCURACY,
                    )
                    results[instr] = {
                        "status": "below_threshold",
                        "oos_accuracy": wf_result.oos_accuracy,
                    }

    return results
