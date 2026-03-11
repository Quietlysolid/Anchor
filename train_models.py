"""Train XGBoost models from CSV data and save to /app/models/."""
import pandas as pd
import numpy as np
import os
from pathlib import Path
from anchor.ml.feature_engineer import FeatureEngineer
from anchor.ml.xgb_classifier import XGBDirectionClassifier

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD"]
SL_ATR = 1.5
TP_ATR = 1.5   # 1:1 R:R for labeling — more samples, less noise
FWD = 24       # 24 bars max (1 trading day)
MODEL_DIR = Path("/app/models")
DATA_DIR = Path("/tmp/data")
MIN_ACC = 0.58


def wilder_atr(highs, lows, closes, period=14):
    n = len(closes)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    atr = np.zeros(n)
    atr[period] = tr[1:period + 1].mean()
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    for i in range(period, -1, -1):
        atr[i] = atr[period]
    return atr


def make_labels(df):
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    atr = wilder_atr(highs, lows, closes)
    labels = np.full(len(df), -1, dtype=int)
    for i in range(len(df) - FWD - 1):
        if atr[i] == 0:
            continue
        long_sl = closes[i] - SL_ATR * atr[i]
        long_tp = closes[i] + TP_ATR * atr[i]
        short_sl = closes[i] + SL_ATR * atr[i]
        short_tp = closes[i] - TP_ATR * atr[i]
        for j in range(i + 1, i + FWD + 1):
            long_stopped = lows[j] <= long_sl
            long_hit = highs[j] >= long_tp
            short_stopped = highs[j] >= short_sl
            short_hit = lows[j] <= short_tp
            if long_stopped and short_stopped:
                break
            if long_hit and not long_stopped:
                labels[i] = 1
                break
            if short_hit and not short_stopped:
                labels[i] = 0
                break
            if long_stopped or short_stopped:
                break
    return labels


def main():
    engineer = FeatureEngineer()
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    for pair in PAIRS:
        csv_h1 = DATA_DIR / f"{pair}_H1.csv"
        csv_h4 = DATA_DIR / f"{pair}_H4.csv"
        if not csv_h1.exists():
            print(f"SKIP {pair} — {csv_h1} not found")
            continue

        print(f"\n{'=' * 50}")
        print(f"Training {pair}...")
        df = pd.read_csv(csv_h1, parse_dates=["time"])
        df = df.set_index("time").sort_index()
        print(f"  H1 bars: {len(df)}")

        df_h4 = None
        if csv_h4.exists():
            df_h4 = pd.read_csv(csv_h4, parse_dates=["time"])
            df_h4 = df_h4.set_index("time").sort_index()
            print(f"  H4 bars: {len(df_h4)}")

        labels = make_labels(df)
        n_long = (labels == 1).sum()
        n_short = (labels == 0).sum()
        print(f"  Labels — LONG: {n_long}, SHORT: {n_short}, skip: {(labels==-1).sum()}")

        feats, feat_labels = [], []
        for i in range(60, len(df) - FWD - 1):
            if labels[i] == -1:
                continue
            window_h1 = df.iloc[i - 60:i]
            window_h4 = None
            if df_h4 is not None:
                current_time = df.index[i]
                mask = df_h4.index <= current_time
                if mask.sum() >= 55:
                    window_h4 = df_h4[mask].iloc[-55:]
            try:
                f = engineer.build(pair, window_h1, window_h4)
                feats.append(f)
                feat_labels.append(labels[i])
            except Exception:
                pass

        if len(feats) < 200:
            print(f"  SKIP — only {len(feats)} labeled samples")
            continue

        X = np.array(feats)
        y = np.array(feat_labels)
        print(f"  Samples: {len(X)}, classes: {np.bincount(y)}")

        split = int(len(X) * 0.8)
        X_train, y_train = X[:split], y[:split]
        X_oos, y_oos = X[split:], y[split:]

        model_path = MODEL_DIR / f"{pair}_xgb.pkl"
        clf = XGBDirectionClassifier(model_path=model_path)
        clf.fit(X_train, y_train)

        confidences = [clf.predict(x.reshape(1, -1)) for x in X_oos]
        preds = np.array([1 if d == "LONG" else 0 for _, d in confidences])
        acc = (preds == y_oos).mean()
        print(f"  OOS accuracy: {acc:.3f} (need >= {MIN_ACC})")

        if acc >= MIN_ACC:
            clf.save(model_path)
            print(f"  SAVED -> {model_path}")
        else:
            print(f"  Below threshold — not saved")

    print("\nAll pairs done.")


if __name__ == "__main__":
    main()
