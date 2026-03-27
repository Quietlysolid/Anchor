"""Download daily futures history for Anchor Futures v1 research."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import structlog

from anchor.futures.contracts import get_futures_market

logger = structlog.get_logger(__name__)

_YAHOO_SYMBOL_BY_MARKET = {
    "MES": "ES=F",
    "MNQ": "NQ=F",
    "ZN": "ZN=F",
    "MGC": "GC=F",
    "MCL": "CL=F",
}


def _download_history(yahoo_symbol: str, period: str) -> pd.DataFrame:
    import yfinance as yf

    hist = yf.Ticker(yahoo_symbol).history(period=period, auto_adjust=False)
    if hist is None or hist.empty:
        raise ValueError(f"No data returned for {yahoo_symbol}")

    frame = hist.reset_index().rename(
        columns={
            "Date": "time",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    keep = [col for col in ["time", "open", "high", "low", "close", "volume"] if col in frame.columns]
    return frame[keep].dropna(subset=["time", "close"]).sort_values("time")


def download_futures_history(markets: list[str], output_dir: str, period: str = "10y") -> list[Path]:
    output_paths: list[Path] = []
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for market_id in markets:
        market = get_futures_market(market_id)
        yahoo_symbol = _YAHOO_SYMBOL_BY_MARKET.get(market.market_id)
        if not yahoo_symbol:
            raise ValueError(f"No Yahoo futures mapping defined for {market.market_id}")

        logger.info("futures_history_download_start", market=market.market_id, yahoo_symbol=yahoo_symbol, period=period)
        frame = _download_history(yahoo_symbol=yahoo_symbol, period=period)
        out_path = out_dir / f"{market.market_id}_D.csv"
        frame.to_csv(out_path, index=False)
        logger.info(
            "futures_history_saved",
            market=market.market_id,
            rows=len(frame),
            start=str(frame["time"].iloc[0]),
            end=str(frame["time"].iloc[-1]),
            path=str(out_path),
            source_symbol=yahoo_symbol,
        )
        output_paths.append(out_path)

    return output_paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Download daily futures history for Anchor Futures v1")
    parser.add_argument("--markets", default="MES,MNQ,ZN,MGC,MCL", help="Comma-separated futures market ids")
    parser.add_argument("--output-dir", default="data", help="Output directory for <MARKET>_D.csv files")
    parser.add_argument("--period", default="10y", help="Yahoo Finance history period, e.g. 5y, 10y, max")
    args = parser.parse_args()

    markets = [part.strip().upper() for part in args.markets.split(",") if part.strip()]
    download_futures_history(markets=markets, output_dir=args.output_dir, period=args.period)


if __name__ == "__main__":
    main()
