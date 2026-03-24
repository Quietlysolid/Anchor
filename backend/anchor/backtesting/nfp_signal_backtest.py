"""Focused research backtest for the surviving post-NFP continuation hypothesis."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import numpy as np
import pandas as pd

from anchor.backtesting.event_study import _load_h1_csv, _tradable_bar_after
from anchor.backtesting.nfp_drift_backtest import (
    _agreement_label,
    _fetch_nfp_bundles,
    _relative_surprise,
    _signed_trade_return,
    _surprise_bucket,
    _usd_direction_for_pair,
)
from anchor.backtesting.validation_utils import compute_trade_metrics

DATA_DIR = Path("/app/data")
_ROUND_TRIP_SPREAD_PIPS = {
    "EUR_USD": 0.6,
    "GBP_USD": 0.8,
    "USD_CAD": 1.0,
    "USD_JPY": 0.6,
}


def _summarize(group: pd.DataFrame, pnl_col: str = "ret_pips_net") -> dict[str, float | int]:
    pnl = group[pnl_col].astype(float).values
    metrics = compute_trade_metrics(pnl)
    return {
        "n_trades": int(metrics["n_trades"]),
        "mean_ret_pips": round(float(np.mean(pnl)), 2),
        "median_ret_pips": round(float(np.median(pnl)), 2),
        "wr": round(metrics["wr"] * 100.0, 1),
        "pf": round(metrics["pf"], 3),
        "maxdd": round(metrics["maxdd"] * 100.0, 2),
    }


async def _run(args) -> int:
    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    allowed_agreements = {x.strip().upper() for x in args.agreements.split(",") if x.strip()}
    allowed_buckets = {x.strip().upper() for x in args.sizes.split(",") if x.strip()}

    h1_data: dict[str, pd.DataFrame] = {}
    for pair in pairs:
        try:
            h1_data[pair] = _load_h1_csv(pair, Path(args.data_dir))
        except FileNotFoundError:
            print(f"SKIP: missing H1 CSV for {pair}")

    if not h1_data:
        print("ERROR: no H1 data loaded")
        return 1

    bundles = await _fetch_nfp_bundles(args.start, args.end)
    if not bundles:
        print("No NFP release bundles found.")
        return 0

    rows: list[dict[str, object]] = []
    for bundle in bundles:
        from anchor.signals.economic_surprise import _surprise_sign

        nfp_sign = _surprise_sign(bundle.nfp.actual, bundle.nfp.forecast)
        if nfp_sign is None or abs(nfp_sign) < 1e-12:
            continue

        unemployment_sign = None
        if bundle.unemployment is not None:
            raw_sign = _surprise_sign(bundle.unemployment.actual, bundle.unemployment.forecast)
            unemployment_sign = (-raw_sign) if raw_sign is not None else None

        earnings_sign = None
        if bundle.earnings is not None:
            earnings_sign = _surprise_sign(bundle.earnings.actual, bundle.earnings.forecast)

        agreement, support_count, conflict_count = _agreement_label(
            nfp_sign, unemployment_sign, earnings_sign
        )
        surprise_mag = _relative_surprise(bundle.nfp.actual, bundle.nfp.forecast)
        surprise_bucket = _surprise_bucket(surprise_mag, args.big_surprise_threshold)

        if agreement not in allowed_agreements or surprise_bucket not in allowed_buckets:
            continue

        for pair, df_h1 in h1_data.items():
            entry_time = _tradable_bar_after(df_h1, bundle.event_time)
            if entry_time is None:
                continue
            direction = _usd_direction_for_pair(pair, nfp_sign)
            if direction is None:
                continue
            trade = _signed_trade_return(df_h1, pair, direction, entry_time, args.horizon_hours)
            if trade is None:
                continue
            cost_pips = _ROUND_TRIP_SPREAD_PIPS.get(pair, 0.0) * args.spread_mult + 2.0 * args.slippage_pips
            ret_pips_gross = float(trade["ret_pips"])
            ret_pips_net = ret_pips_gross - cost_pips
            rows.append(
                {
                    "pair": pair,
                    "event_time": bundle.event_time,
                    "entry_time": entry_time,
                    "year": entry_time.year,
                    "horizon_hours": args.horizon_hours,
                    "agreement": agreement,
                    "surprise_bucket": surprise_bucket,
                    "support_count": support_count,
                    "conflict_count": conflict_count,
                    "ret_pips": ret_pips_gross,
                    "ret_pips_net": ret_pips_net,
                    "cost_pips": cost_pips,
                    "ret_pct": trade["ret_pct"],
                }
            )

    if not rows:
        print("No observations matched the focused NFP rule set.")
        return 0

    obs = pd.DataFrame(rows).sort_values(["pair", "entry_time"])

    overall_rows = []
    for pair, group in obs.groupby("pair"):
        summary = _summarize(group)
        overall_rows.append({"pair": pair, **summary})
    overall = pd.DataFrame(overall_rows).sort_values(
        ["pf", "mean_ret_pips", "wr"], ascending=[False, False, False]
    )

    yearly_rows = []
    for (pair, year), group in obs.groupby(["pair", "year"]):
        summary = _summarize(group)
        yearly_rows.append({"pair": pair, "year": int(year), **summary})
    yearly = pd.DataFrame(yearly_rows).sort_values(["pair", "year"])

    print(f"\n{'=' * 88}")
    print("NFP SIGNAL BACKTEST")
    print(f"{'=' * 88}")
    print(f"Pairs        : {', '.join(sorted(h1_data.keys()))}")
    print(f"Horizon hrs  : {args.horizon_hours}")
    print(f"Agreements   : {', '.join(sorted(allowed_agreements))}")
    print(f"Sizes        : {', '.join(sorted(allowed_buckets))}")
    print(f"Spread mult  : {args.spread_mult}")
    print(f"Slippage     : {args.slippage_pips:.2f} pips/side")
    print(f"Observations : {len(obs)} from {obs['event_time'].nunique()} releases")
    print(f"{'=' * 88}")
    print(f"{'Pair':<10} {'N':>4} {'MeanPips':>10} {'MedPips':>9} {'WR':>6} {'PF':>7} {'MaxDD':>7}")
    for _, row in overall.iterrows():
        print(
            f"{row['pair']:<10} {int(row['n_trades']):>4} {row['mean_ret_pips']:>10.2f} "
            f"{row['median_ret_pips']:>9.2f} {row['wr']:>5.1f}% {row['pf']:>7.3f} {row['maxdd']:>6.2f}%"
        )
    print(f"{'=' * 88}\n")

    print(f"{'=' * 88}")
    print("YEARLY STABILITY")
    print(f"{'=' * 88}")
    print(f"{'Pair':<10} {'Year':>4} {'N':>4} {'MeanPips':>10} {'WR':>6} {'PF':>7} {'MaxDD':>7}")
    for _, row in yearly.iterrows():
        print(
            f"{row['pair']:<10} {int(row['year']):>4} {int(row['n_trades']):>4} "
            f"{row['mean_ret_pips']:>10.2f} {row['wr']:>5.1f}% {row['pf']:>7.3f} {row['maxdd']:>6.2f}%"
        )
    print(f"{'=' * 88}\n")

    if args.export_csv:
        export_path = Path(args.export_csv)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        obs.to_csv(export_path.with_suffix(".observations.csv"), index=False)
        overall.to_csv(export_path.with_suffix(".overall.csv"), index=False)
        yearly.to_csv(export_path.with_suffix(".yearly.csv"), index=False)
        print(f"Saved: {export_path.with_suffix('.observations.csv')}")
        print(f"Saved: {export_path.with_suffix('.overall.csv')}")
        print(f"Saved: {export_path.with_suffix('.yearly.csv')}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Focused NFP continuation signal backtest")
    parser.add_argument("--pairs", default="EUR_USD,GBP_USD,USD_CAD,USD_JPY")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--horizon-hours", type=int, default=24)
    parser.add_argument("--agreements", default="ALIGNED")
    parser.add_argument("--sizes", default="BIG")
    parser.add_argument("--big-surprise-threshold", type=float, default=0.20)
    parser.add_argument("--spread-mult", type=float, default=1.0)
    parser.add_argument("--slippage-pips", type=float, default=0.0)
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--export-csv", default=None)
    raise_code = asyncio.run(_run(parser.parse_args()))
    raise SystemExit(raise_code)


if __name__ == "__main__":
    main()
