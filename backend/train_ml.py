"""
Train XGBoost direction classifiers on 4 years of OANDA H1 data.

Run on VPS:
    docker exec anchor_engine python /app/train_ml.py

Models saved to /app/models/<INSTRUMENT>_xgb.pkl
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import oandapyV20
import oandapyV20.endpoints.instruments as instruments_ep

sys.path.insert(0, "/app")

from anchor.config import get_settings
from anchor.ml.feature_engineer import FeatureEngineer
from anchor.ml.xgb_classifier import XGBDirectionClassifier
from anchor.ml.walk_forward import walk_forward_validate

settings = get_settings()

INSTRUMENTS = settings.instruments
START_DATE = datetime(2022, 1, 1, tzinfo=timezone.utc)
CANDLES_PER_REQUEST = 5000
MODEL_DIR = Path("/app/models")
LABEL_FORWARD_BARS = 3   # predict direction 3H ahead
MIN_OOS_ACCURACY = 0.52  # lower bar for initial training (market is noisy)


def fetch_candles(client, instrument: str, granularity: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Fetch all candles using from+count pagination."""
    all_rows = []
    current = start

    while current < end:
        params = {
            "granularity": granularity,
            "from": current.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": CANDLES_PER_REQUEST,
            "price": "M",
        }
        try:
            ep = instruments_ep.InstrumentsCandles(instrument, params=params)
            client.request(ep)
            candles = ep.response.get("candles", [])
            if not candles:
                break
            last_time_str = candles[-1]["time"]
            for c in candles:
                c_dt = datetime.fromisoformat(c["time"].replace("Z", "+00:00"))
                if c_dt > end:
                    break
                if c.get("complete"):
                    mid = c["mid"]
                    all_rows.append({
                        "time":   c["time"],
                        "open":   float(mid["o"]),
                        "high":   float(mid["h"]),
                        "low":    float(mid["l"]),
                        "close":  float(mid["c"]),
                        "volume": int(c.get("volume", 0)),
                    })
            last_dt = datetime.fromisoformat(last_time_str.replace("Z", "+00:00"))
            if last_dt <= current:
                break
            current = last_dt
        except Exception as exc:
            print(f"  [warn] {instrument} {granularity} chunk failed: {exc}")
            break
        time.sleep(0.3)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    return df


def make_labels(closes: np.ndarray, forward_bars: int = LABEL_FORWARD_BARS) -> np.ndarray:
    """1 if price rose N bars later, 0 if fell. -1 = invalid (end of series)."""
    future = np.roll(closes, -forward_bars)
    labels = (future > closes).astype(int)
    labels[-forward_bars:] = -1
    return labels


def build_features(df_h1: pd.DataFrame, df_h4: pd.DataFrame, instrument: str, engineer: FeatureEngineer):
    """Slide a 60-bar window across H1, aligning H4 data."""
    feature_rows = []
    timestamps = []

    for i in range(60, len(df_h1)):
        window_h1 = df_h1.iloc[i - 60: i].copy()

        # Find H4 bars up to this H1 bar's time
        bar_time = df_h1.iloc[i]["time"]
        if not df_h4.empty:
            window_h4 = df_h4[df_h4["time"] <= bar_time].tail(100)
            window_h4 = window_h4 if len(window_h4) >= 55 else None
        else:
            window_h4 = None

        # Set index to time for feature engineer
        window_h1 = window_h1.set_index("time")
        if window_h4 is not None:
            window_h4 = window_h4.set_index("time")

        try:
            feat = engineer.build(instrument, window_h1, window_h4)
            feature_rows.append(feat)
            timestamps.append(bar_time)
        except Exception:
            continue

    return np.array(feature_rows, dtype=np.float32), np.array(timestamps)


def main():
    client = oandapyV20.API(
        access_token=settings.oanda_api_key,
        environment=settings.oanda_environment,
    )
    end = datetime.now(timezone.utc)
    engineer = FeatureEngineer()
    feature_names = engineer.get_feature_names()
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*70}")
    print(f"  ANCHOR ML TRAINING  |  {START_DATE.date()} → {end.date()}")
    print(f"{'='*70}\n")

    results = {}

    for instrument in INSTRUMENTS:
        print(f"── {instrument} ────────────────────────────────────────────────")

        print("  Fetching H1...", end="", flush=True)
        df_h1 = fetch_candles(client, instrument, "H1", START_DATE, end)
        print(f" {len(df_h1)} candles")

        print("  Fetching H4...", end="", flush=True)
        df_h4 = fetch_candles(client, instrument, "H4", START_DATE, end)
        print(f" {len(df_h4)} candles")

        if df_h1.empty or len(df_h1) < 500:
            print("  [skip] Insufficient data\n")
            continue

        print("  Building features...", end="", flush=True)
        X, timestamps = build_features(df_h1, df_h4, instrument, engineer)
        closes = df_h1["close"].values[60: 60 + len(X)]
        labels = make_labels(closes)
        valid = labels >= 0
        X = X[valid]
        y = labels[valid]
        ts = timestamps[valid].astype("datetime64[M]")
        print(f" {len(X)} samples, {X.shape[1]} features")

        if len(X) < 500:
            print("  [skip] Too few feature rows\n")
            continue

        # Walk-forward validation
        print("  Walk-forward validation...")
        wf = walk_forward_validate(X, y, ts, "xgb", feature_names)
        oos_acc = wf.oos_accuracy
        print(f"  OOS Accuracy: {oos_acc*100:.1f}% over {wf.oos_samples} samples ({len(wf.folds)} folds)")

        # Train final model on all data
        clf = XGBDirectionClassifier()
        clf.fit(X, y, feature_names)

        model_path = MODEL_DIR / f"{instrument}_xgb.pkl"
        clf.save(model_path)
        print(f"  Saved → {model_path}")

        verdict = "✓ USABLE" if oos_acc >= MIN_OOS_ACCURACY else "⚠ WEAK"
        results[instrument] = {"oos_accuracy": oos_acc, "samples": len(X), "verdict": verdict}
        print(f"  {verdict}\n")

    print(f"\n{'='*70}")
    print("  TRAINING SUMMARY")
    print(f"{'='*70}")
    print(f"{'Pair':<12} {'OOS Acc':>8} {'Samples':>9}  Verdict")
    print("-" * 45)
    for instr, r in results.items():
        print(f"{instr:<12} {r['oos_accuracy']*100:>7.1f}% {r['samples']:>9}  {r['verdict']}")
    print(f"{'='*70}\n")
    print(f"Models saved to {MODEL_DIR}")
    print("Now run: docker exec anchor_engine python /app/run_backtest.py --with-ml\n")


if __name__ == "__main__":
    main()
