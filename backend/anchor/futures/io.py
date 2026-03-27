"""Data loading helpers for Anchor Futures v1."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def read_daily_futures_csv(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    if "time" not in df.columns or "close" not in df.columns:
        raise ValueError(f"{csv_path} must contain 'time' and 'close' columns")
    df["time"] = pd.to_datetime(df["time"], utc=True)
    keep = [col for col in ["time", "open", "high", "low", "close", "volume"] if col in df.columns]
    return df[keep].dropna(subset=["time", "close"]).sort_values("time")


def load_daily_market_closes(data_dir: str, markets: list[str]) -> pd.DataFrame:
    closes: dict[str, pd.Series] = {}
    for market in markets:
        csv_path = Path(data_dir) / f"{market}_D.csv"
        if not csv_path.exists():
            continue
        df = read_daily_futures_csv(csv_path).set_index("time")
        closes[market] = df["close"]
    if not closes:
        raise ValueError("No usable futures daily CSVs were found for the requested markets")
    frame = pd.DataFrame(closes).sort_index()
    frame.index = pd.to_datetime(frame.index, utc=True)
    return frame.dropna(how="all")
