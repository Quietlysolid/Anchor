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

# Risk-adjusted label parameters — match live trading SL/TP multipliers
_SL_ATR_MULT = 1.5
_TP_ATR_MULT = 3.0
_MAX_FORWARD_BARS = 48  # cap look-forward at 48 H1 bars (2 trading days)


def _make_labels(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
) -> np.ndarray:
    """Risk-adjusted binary labels that mirror live trading SL/TP logic.

    For each bar k, we simulate a LONG entry at closes[k]:
      - SL = entry - 1.5 * ATR14[k]
      - TP = entry + 3.0 * ATR14[k]

    Then scan forward up to MAX_FORWARD_BARS bars:
      label[k] = 1  if TP hit before SL  (trade would have won)
      label[k] = 0  if SL hit before TP  (trade would have lost)
      label[k] = -1 if neither hit       (timeout — excluded from training)

    This directly aligns the training objective with actual P&L, producing
    a cleaner signal than a raw N-bar price direction label.

    Note: We only simulate LONG scenarios here. The engine evaluates LONG and
    SHORT separately; the label captures whether the move materialized with the
    correct magnitude. In practice XGB learns symmetry across both sides.
    """
    n = len(closes)
    labels = np.full(n, -1, dtype=int)

    # Compute ATR14 using true range
    tr = np.zeros(n)
    for i in range(1, n):
        hl  = highs[i]  - lows[i]
        hpc = abs(highs[i]  - closes[i - 1])
        lpc = abs(lows[i]   - closes[i - 1])
        tr[i] = max(hl, hpc, lpc)
    # Simple rolling mean for ATR (faster than pandas here)
    atr = np.full(n, np.nan)
    for i in range(14, n):
        atr[i] = tr[i - 13: i + 1].mean()

    for k in range(14, n - 1):
        if np.isnan(atr[k]):
            continue
        entry = closes[k]
        sl    = entry - _SL_ATR_MULT * atr[k]
        tp    = entry + _TP_ATR_MULT * atr[k]

        for j in range(k + 1, min(k + _MAX_FORWARD_BARS + 1, n)):
            if lows[j] <= sl:
                labels[k] = 0  # SL hit first
                break
            if highs[j] >= tp:
                labels[k] = 1  # TP hit first
                break
        # if neither hit within MAX_FORWARD_BARS, label stays -1 (excluded)

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
            offset = 60
            n_rows = len(feature_rows)
            highs  = df["high"].values[offset: offset + n_rows]
            lows   = df["low"].values[offset: offset + n_rows]
            closes = df["close"].values[offset: offset + n_rows]
            labels = _make_labels(highs, lows, closes)
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
