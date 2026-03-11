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
from anchor.ml.xgb_classifier import XGBDirectionClassifier, DEFAULT_MODEL_DIR
from anchor.ml.lgbm_classifier import LGBMDirectionClassifier
from anchor.ml.model_registry import ModelRegistry
from anchor.ml.ood_detector import OODDetector

logger = structlog.get_logger(__name__)

LOOKBACK_YEARS = 2
MIN_OOS_ACCURACY = 0.58  # must beat this to replace production model

# Risk-adjusted label parameters — match live trading SL/TP multipliers
_SL_ATR_MULT = 1.5
_TP_ATR_MULT = 3.0
_MAX_FORWARD_BARS = 48  # cap look-forward at 48 H1 bars (2 trading days)


def _wilder_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder's smoothed ATR — matches the `ta` library used in live trading."""
    n = len(closes)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
    atr = np.full(n, np.nan)
    if n > period:
        atr[period] = float(np.mean(tr[1: period + 1]))
        alpha = 1.0 / period
        for i in range(period + 1, n):
            atr[i] = alpha * tr[i] + (1.0 - alpha) * atr[i - 1]
    return atr


def _make_labels(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
) -> np.ndarray:
    """Risk-adjusted binary labels that mirror live trading SL/TP logic.

    For each bar k we simulate BOTH a LONG and a SHORT entry at closes[k]:
      LONG:  SL = entry - 1.5*ATR,  TP = entry + 3.0*ATR
      SHORT: SL = entry + 1.5*ATR,  TP = entry - 3.0*ATR

    We scan forward up to MAX_FORWARD_BARS and record whichever side wins.

      label[k] = 1  if LONG TP hit first   (bullish bar)
      label[k] = 0  if SHORT TP hit first  (bearish bar)
      label[k] = -1 if neither side resolves (timeout — excluded)

    This correctly labels both directions symmetrically so XGBoost is not
    trained with a built-in bullish bias. Previously only LONG was simulated,
    which caused SHORT-signal bars where the LONG also won to be mislabelled.

    ATR uses Wilder's smoothing (α=1/14) to match the live ta-library value,
    eliminating the SL/TP distance mismatch during volatile periods.
    """
    n = len(closes)
    labels = np.full(n, -1, dtype=int)
    atr = _wilder_atr(highs, lows, closes)

    for k in range(14, n - 1):
        if np.isnan(atr[k]):
            continue
        entry      = closes[k]
        long_sl    = entry - _SL_ATR_MULT * atr[k]
        long_tp    = entry + _TP_ATR_MULT * atr[k]
        short_sl   = entry + _SL_ATR_MULT * atr[k]
        short_tp   = entry - _TP_ATR_MULT * atr[k]

        long_result  = -1   # -1 = unresolved
        short_result = -1

        for j in range(k + 1, min(k + _MAX_FORWARD_BARS + 1, n)):
            h, l = highs[j], lows[j]

            if long_result == -1:
                if l <= long_sl:
                    long_result = 0   # long stopped out
                elif h >= long_tp:
                    long_result = 1   # long TP hit

            if short_result == -1:
                if h >= short_sl:
                    short_result = 0  # short stopped out
                elif l <= short_tp:
                    short_result = 1  # short TP hit

            if long_result != -1 and short_result != -1:
                break

        # Assign label: LONG win = 1, SHORT win = 0, both/neither = excluded
        if long_result == 1 and short_result != 1:
            labels[k] = 1
        elif short_result == 1 and long_result != 1:
            labels[k] = 0
        # if both sides hit TP (very rare, e.g. large candle) → exclude (-1)
        # if neither side resolved within the window → exclude (-1)

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
                    "open": [float(c.open) for c in candles],
                    "high": [float(c.high) for c in candles],
                    "low": [float(c.low) for c in candles],
                    "close": [float(c.close) for c in candles],
                    "volume": [float(c.volume or 0) for c in candles],
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
                    model_path = DEFAULT_MODEL_DIR / f"{instr}_xgb.pkl"
                    clf = XGBDirectionClassifier(model_path=model_path)
                    clf.fit(X, y, feature_names)

                    # Fit OOD detector on training features so the gate is live
                    ood = OODDetector()
                    ood.fit(X)
                    ood_path = DEFAULT_MODEL_DIR / f"{instr}_ood.pkl"
                    ood.save(ood_path)

                    # Log and register
                    run_id = mlflow.active_run().info.run_id
                    clf.save(model_path)
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
