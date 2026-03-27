"""CLI helper to print current Futures v1 target positions."""
from __future__ import annotations

import argparse
import json

import pandas as pd

from anchor.futures.strategy import build_futures_v1_targets


def main() -> None:
    parser = argparse.ArgumentParser(description="Print current Anchor Futures v1 targets")
    parser.add_argument("--data-dir", default="/app/data")
    parser.add_argument("--markets", default="", help="Optional comma-separated override")
    parser.add_argument("--as-of", default=None, help="Optional ISO date override")
    args = parser.parse_args()

    markets = [part.strip().upper() for part in args.markets.split(",") if part.strip()] or None
    as_of = pd.Timestamp(args.as_of, tz="UTC") if args.as_of else None
    targets = build_futures_v1_targets(data_dir=args.data_dir, markets=markets, as_of=as_of)
    print(
        json.dumps(
            [
                {
                    "market": target.market,
                    "contract": target.contract,
                    "signal": target.signal,
                    "weight": round(target.weight, 6),
                }
                for target in targets
            ],
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
