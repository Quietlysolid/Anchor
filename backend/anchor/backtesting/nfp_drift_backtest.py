"""Research backtest for post-NFP FX drift on H1 data.

Builds exact USD payroll release bundles from the economic calendar table and
tests a few clean hypotheses:
  - immediate continuation after the release
  - immediate fade after the release
  - delayed continuation after one confirming H1 bar

The output is intended for research triage, not production execution. It keeps
the design explicit and pair-specific so we can decide whether there is a real
event-conditioned edge before integrating anything into live logic.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import and_, select

from anchor.backtesting.event_study import _load_h1_csv, _tradable_bar_after, _utc_timestamp
from anchor.backtesting.validation_utils import compute_trade_metrics
from anchor.database.engine import close_db, get_session, init_db
from anchor.database.models import EconomicEvent
from anchor.signals.economic_surprise import _PAIR_CURRENCIES, _parse_value, _surprise_sign
from anchor.utils.math_utils import get_pip_size

DATA_DIR = Path("/app/data")
NFP_EVENT = "Non-Farm Employment Change"
UNEMPLOYMENT_EVENT = "Unemployment Rate"
EARNINGS_EVENT = "Average Hourly Earnings m/m"
DEFAULT_WINDOWS = [4, 8, 24]


@dataclass
class ReleaseBundle:
    event_time: pd.Timestamp
    nfp: EconomicEvent
    unemployment: EconomicEvent | None
    earnings: EconomicEvent | None


def _fetch_event(row_map: dict[str, EconomicEvent], name: str) -> EconomicEvent | None:
    return row_map.get(name)


async def _fetch_nfp_bundles(start: str, end: str) -> list[ReleaseBundle]:
    await init_db()
    start_ts = pd.Timestamp(start, tz="UTC").to_pydatetime()
    end_ts = pd.Timestamp(end, tz="UTC").to_pydatetime()

    async with get_session() as session:
        result = await session.execute(
            select(EconomicEvent)
            .where(
                and_(
                    EconomicEvent.currency == "USD",
                    EconomicEvent.impact == "HIGH",
                    EconomicEvent.event_time >= start_ts,
                    EconomicEvent.event_time <= end_ts,
                    EconomicEvent.event_name.in_([NFP_EVENT, UNEMPLOYMENT_EVENT, EARNINGS_EVENT]),
                    EconomicEvent.actual.isnot(None),
                    EconomicEvent.forecast.isnot(None),
                )
            )
            .order_by(EconomicEvent.event_time, EconomicEvent.event_name)
        )
        rows = list(result.scalars().all())

    await close_db()

    grouped: dict[pd.Timestamp, dict[str, EconomicEvent]] = {}
    for event in rows:
        event_ts = _utc_timestamp(event.event_time)
        grouped.setdefault(event_ts, {})[event.event_name] = event

    bundles: list[ReleaseBundle] = []
    for event_ts, row_map in sorted(grouped.items()):
        nfp = _fetch_event(row_map, NFP_EVENT)
        if nfp is None:
            continue
        bundles.append(
            ReleaseBundle(
                event_time=event_ts,
                nfp=nfp,
                unemployment=_fetch_event(row_map, UNEMPLOYMENT_EVENT),
                earnings=_fetch_event(row_map, EARNINGS_EVENT),
            )
        )
    return bundles


def _relative_surprise(actual: str | None, forecast: str | None) -> float | None:
    actual_val = _parse_value(actual)
    forecast_val = _parse_value(forecast)
    if actual_val is None or forecast_val is None:
        return None
    scale = abs(forecast_val)
    if scale <= 1e-12:
        return None
    return abs(actual_val - forecast_val) / scale


def _usd_direction_for_pair(pair: str, usd_sign: float) -> str | None:
    if abs(usd_sign) < 1e-12:
        return None
    base_ccy, quote_ccy = _PAIR_CURRENCIES[pair]
    if base_ccy == "USD":
        return "LONG" if usd_sign > 0 else "SHORT"
    if quote_ccy == "USD":
        return "SHORT" if usd_sign > 0 else "LONG"
    return None


def _signed_trade_return(
    df_h1: pd.DataFrame,
    pair: str,
    direction: str,
    trade_time: pd.Timestamp,
    horizon_hours: int,
) -> dict[str, float | pd.Timestamp] | None:
    if trade_time not in df_h1.index:
        return None

    exit_cutoff = trade_time + pd.Timedelta(hours=horizon_hours)
    exit_idx = df_h1.index.searchsorted(exit_cutoff, side="left")
    if exit_idx >= len(df_h1.index):
        return None

    entry_open = float(df_h1.loc[trade_time, "open"])
    exit_close = float(df_h1.iloc[exit_idx]["close"])
    raw_move = exit_close - entry_open
    signed_move = raw_move if direction == "LONG" else -raw_move
    pip_size = get_pip_size(pair)

    return {
        "exit_time": pd.Timestamp(df_h1.index[exit_idx]),
        "entry_open": entry_open,
        "exit_close": exit_close,
        "ret_pips": signed_move / pip_size,
        "ret_pct": (signed_move / entry_open) * 100.0 if entry_open > 0 else 0.0,
    }


def _bar_confirms_direction(df_h1: pd.DataFrame, bar_time: pd.Timestamp, direction: str) -> bool:
    if bar_time not in df_h1.index:
        return False
    bar = df_h1.loc[bar_time]
    if direction == "LONG":
        return float(bar["close"]) > float(bar["open"])
    return float(bar["close"]) < float(bar["open"])


def _agreement_label(
    headline_sign: float,
    unemployment_sign: float | None,
    earnings_sign: float | None,
) -> tuple[str, int, int]:
    support = 0
    conflict = 0
    for sign in [unemployment_sign, earnings_sign]:
        if sign is None or abs(sign) < 1e-12 or abs(headline_sign) < 1e-12:
            continue
        if np.sign(sign) == np.sign(headline_sign):
            support += 1
        else:
            conflict += 1

    if abs(headline_sign) < 1e-12:
        return "FLAT", support, conflict
    if conflict > 0:
        return "CONFLICT", support, conflict
    if support > 0:
        return "ALIGNED", support, conflict
    return "SOLO", support, conflict


def _surprise_bucket(magnitude: float | None, threshold: float) -> str:
    if magnitude is None:
        return "UNK"
    return "BIG" if magnitude >= threshold else "STD"


def _observations_to_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    grouped = df.groupby(["pair", "setup", "horizon_hours", "agreement", "surprise_bucket"])
    for (pair, setup, horizon_hours, agreement, surprise_bucket), group in grouped:
        pnl = group["ret_pips"].astype(float).values
        metrics = compute_trade_metrics(pnl)
        rows.append(
            {
                "pair": pair,
                "setup": setup,
                "horizon_hours": horizon_hours,
                "agreement": agreement,
                "surprise_bucket": surprise_bucket,
                "n_trades": int(metrics["n_trades"]),
                "mean_ret_pips": round(float(np.mean(pnl)), 2),
                "median_ret_pips": round(float(np.median(pnl)), 2),
                "wr": round(metrics["wr"] * 100.0, 1),
                "pf": round(metrics["pf"], 3),
                "maxdd": round(metrics["maxdd"] * 100.0, 2),
                "mean_ret_pct": round(float(group["ret_pct"].astype(float).mean()), 4),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["pair", "setup", "horizon_hours", "agreement", "surprise_bucket"]
    )


def _focused_quant_summary(
    df: pd.DataFrame,
    setup: str,
    min_trades: int,
) -> pd.DataFrame:
    rows = []
    filtered = df[df["setup"] == setup].copy()
    grouped = filtered.groupby(["pair", "horizon_hours", "agreement", "surprise_bucket"])
    for (pair, horizon_hours, agreement, surprise_bucket), group in grouped:
        pnl = group["ret_pips"].astype(float).values
        metrics = compute_trade_metrics(pnl)
        n_trades = int(metrics["n_trades"])
        if n_trades < min_trades:
            continue

        score = (
            float(np.mean(pnl))
            * (metrics["pf"] if np.isfinite(metrics["pf"]) else 5.0)
            * max(metrics["wr"], 0.01)
        )
        rows.append(
            {
                "pair": pair,
                "horizon_hours": horizon_hours,
                "agreement": agreement,
                "surprise_bucket": surprise_bucket,
                "n_trades": n_trades,
                "mean_ret_pips": round(float(np.mean(pnl)), 2),
                "wr": round(metrics["wr"] * 100.0, 1),
                "pf": round(metrics["pf"], 3),
                "maxdd": round(metrics["maxdd"] * 100.0, 2),
                "score": round(score, 2),
            }
        )

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values(
        ["score", "mean_ret_pips", "pf", "wr"],
        ascending=[False, False, False, False],
    )


async def _run(args) -> int:
    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    windows = [int(w.strip()) for w in args.windows.split(",") if w.strip()]

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

    observations: list[dict[str, object]] = []
    for bundle in bundles:
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

        for pair, df_h1 in h1_data.items():
            immediate_time = _tradable_bar_after(df_h1, bundle.event_time)
            if immediate_time is None:
                continue

            continuation_direction = _usd_direction_for_pair(pair, nfp_sign)
            if continuation_direction is None:
                continue

            fade_direction = "SHORT" if continuation_direction == "LONG" else "LONG"

            setup_entries = [("immediate_continuation", immediate_time, continuation_direction)]
            if args.include_fade:
                setup_entries.append(("immediate_fade", immediate_time, fade_direction))

            if args.include_delayed:
                delayed_idx = df_h1.index.get_indexer([immediate_time])[0] + 1
                if delayed_idx < len(df_h1.index) and _bar_confirms_direction(
                    df_h1, immediate_time, continuation_direction
                ):
                    setup_entries.append(
                        (
                            "delayed_continuation",
                            pd.Timestamp(df_h1.index[delayed_idx]),
                            continuation_direction,
                        )
                    )

            for setup, entry_time, direction in setup_entries:
                for horizon_hours in windows:
                    trade = _signed_trade_return(df_h1, pair, direction, entry_time, horizon_hours)
                    if trade is None:
                        continue
                    observations.append(
                        {
                            "pair": pair,
                            "setup": setup,
                            "event_time": bundle.event_time,
                            "entry_time": entry_time,
                            "exit_time": trade["exit_time"],
                            "horizon_hours": horizon_hours,
                            "direction": direction,
                            "nfp_actual": bundle.nfp.actual,
                            "nfp_forecast": bundle.nfp.forecast,
                            "nfp_surprise_sign": nfp_sign,
                            "nfp_surprise_mag": surprise_mag,
                            "unemployment_actual": bundle.unemployment.actual if bundle.unemployment else None,
                            "unemployment_forecast": bundle.unemployment.forecast if bundle.unemployment else None,
                            "earnings_actual": bundle.earnings.actual if bundle.earnings else None,
                            "earnings_forecast": bundle.earnings.forecast if bundle.earnings else None,
                            "agreement": agreement,
                            "support_count": support_count,
                            "conflict_count": conflict_count,
                            "surprise_bucket": surprise_bucket,
                            "ret_pips": trade["ret_pips"],
                            "ret_pct": trade["ret_pct"],
                        }
                    )

    if not observations:
        print("No NFP observations could be aligned to H1 data.")
        return 0

    obs_df = pd.DataFrame(observations)
    summary = _observations_to_summary(obs_df)

    print(f"\n{'=' * 100}")
    print("NFP DRIFT BACKTEST")
    print(f"{'=' * 100}")
    print(f"Pairs        : {', '.join(sorted(h1_data.keys()))}")
    print(f"Window hrs   : {', '.join(str(w) for w in windows)}")
    print(f"Releases     : {len(bundles)} raw / {obs_df['event_time'].nunique()} usable")
    print(f"Delayed cont : {'on' if args.include_delayed else 'off'}")
    print(f"Fade test    : {'on' if args.include_fade else 'off'}")
    print(f"{'=' * 100}")
    print(
        f"{'Pair':<10} {'Setup':<22} {'H':>3} {'Agree':<9} {'Size':<4} "
        f"{'N':>4} {'MeanPips':>10} {'MedPips':>9} {'WR':>6} {'PF':>7} {'MaxDD':>7}"
    )
    for _, row in summary.iterrows():
        print(
            f"{row['pair']:<10} {row['setup']:<22} {int(row['horizon_hours']):>3} "
            f"{row['agreement']:<9} {row['surprise_bucket']:<4} {int(row['n_trades']):>4} "
            f"{row['mean_ret_pips']:>10.2f} {row['median_ret_pips']:>9.2f} "
            f"{row['wr']:>5.1f}% {row['pf']:>7.3f} {row['maxdd']:>6.2f}%"
        )
    print(f"{'=' * 100}\n")

    focused = _focused_quant_summary(
        obs_df,
        setup=args.focus_setup,
        min_trades=args.focus_min_trades,
    )
    if not focused.empty:
        print(f"{'=' * 100}")
        print("QUANT CUT")
        print(f"{'=' * 100}")
        print(
            f"Focus setup  : {args.focus_setup} | min trades: {args.focus_min_trades} | "
            f"sorted by composite score"
        )
        print(
            f"{'Pair':<10} {'H':>3} {'Agree':<9} {'Size':<4} {'N':>4} "
            f"{'MeanPips':>10} {'WR':>6} {'PF':>7} {'MaxDD':>7} {'Score':>9}"
        )
        for _, row in focused.iterrows():
            print(
                f"{row['pair']:<10} {int(row['horizon_hours']):>3} {row['agreement']:<9} "
                f"{row['surprise_bucket']:<4} {int(row['n_trades']):>4} "
                f"{row['mean_ret_pips']:>10.2f} {row['wr']:>5.1f}% "
                f"{row['pf']:>7.3f} {row['maxdd']:>6.2f}% {row['score']:>9.2f}"
            )
        print(f"{'=' * 100}\n")

    if args.export_csv:
        export_path = Path(args.export_csv)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        obs_df.to_csv(export_path.with_suffix(".observations.csv"), index=False)
        summary.to_csv(export_path.with_suffix(".summary.csv"), index=False)
        if not focused.empty:
            focused.to_csv(export_path.with_suffix(".focus.csv"), index=False)
        print(f"Saved: {export_path.with_suffix('.observations.csv')}")
        print(f"Saved: {export_path.with_suffix('.summary.csv')}")
        if not focused.empty:
            print(f"Saved: {export_path.with_suffix('.focus.csv')}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest post-NFP drift hypotheses on H1 FX data")
    parser.add_argument("--pairs", default="EUR_USD,GBP_USD,USD_JPY,USD_CAD")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--windows", default="4,8,24")
    parser.add_argument("--big-surprise-threshold", type=float, default=0.20)
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--include-delayed", action="store_true")
    parser.add_argument("--include-fade", action="store_true")
    parser.add_argument("--focus-setup", default="immediate_continuation")
    parser.add_argument("--focus-min-trades", type=int, default=2)
    parser.add_argument("--export-csv", default=None)
    raise_code = asyncio.run(_run(parser.parse_args()))
    raise SystemExit(raise_code)


if __name__ == "__main__":
    main()
