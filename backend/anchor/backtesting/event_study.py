"""Generic macro event-study framework for FX pairs.

Loads released economic events from the existing economic calendar table and
measures post-event returns on H1 data across one or more pairs.

Initial implementation uses H1 bars for portability with the current dataset.
Later event-specific studies (for example NFP drift) can reuse this module and
switch to finer data if available.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import and_, select

from anchor.database.engine import close_db, get_session, init_db
from anchor.database.models import EconomicEvent
from anchor.signals.economic_surprise import _PAIR_CURRENCIES, _parse_value, _surprise_sign
from anchor.utils.math_utils import get_pip_size

DATA_DIR = Path("/app/data")
DEFAULT_WINDOWS = [1, 4, 8, 24]


@dataclass
class EventObservation:
    pair: str
    currency: str
    event_name: str
    event_time: pd.Timestamp
    trade_time: pd.Timestamp
    horizon_hours: int
    direction_tag: str
    surprise_sign: float
    surprise_mag: float | None
    ret_pips: float
    ret_pct: float


def _load_h1_csv(pair: str, data_dir: Path) -> pd.DataFrame:
    path = data_dir / f"{pair}_H1.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path, parse_dates=["time"])
    df = df.set_index("time").sort_index()
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df.dropna(subset=["open", "high", "low", "close"])


def _relevant_pairs_for_currency(currency: str, pairs: list[str]) -> list[str]:
    result = []
    for pair in pairs:
        base_quote = _PAIR_CURRENCIES.get(pair)
        if not base_quote:
            continue
        if currency in base_quote:
            result.append(pair)
    return result


def _tradable_bar_after(df_h1: pd.DataFrame, event_time: pd.Timestamp) -> pd.Timestamp | None:
    idx = df_h1.index.searchsorted(event_time, side="left")
    if idx >= len(df_h1.index):
        return None
    return pd.Timestamp(df_h1.index[idx])


def _utc_timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _return_from_event(
    df_h1: pd.DataFrame,
    pair: str,
    currency: str,
    event_time: pd.Timestamp,
    horizon_hours: int,
) -> tuple[pd.Timestamp, float, float, str] | None:
    trade_time = _tradable_bar_after(df_h1, event_time)
    if trade_time is None:
        return None

    exit_cutoff = trade_time + pd.Timedelta(hours=horizon_hours)
    exit_idx = df_h1.index.searchsorted(exit_cutoff, side="left")
    if exit_idx >= len(df_h1.index):
        return None

    entry_open = float(df_h1.loc[trade_time, "open"])
    exit_close = float(df_h1.iloc[exit_idx]["close"])
    pip = get_pip_size(pair)

    base_ccy, quote_ccy = _PAIR_CURRENCIES[pair]
    pair_ret = exit_close - entry_open
    if currency == base_ccy:
        signed_ret = pair_ret
        direction_tag = "base_positive"
    elif currency == quote_ccy:
        signed_ret = -pair_ret
        direction_tag = "quote_positive"
    else:
        return None

    ret_pips = signed_ret / pip
    ret_pct = (signed_ret / entry_open) * 100.0 if entry_open > 0 else 0.0
    return trade_time, ret_pips, ret_pct, direction_tag


async def _fetch_events(
    currencies: list[str],
    start: str,
    end: str,
    impacts: list[str],
    event_query: str | None,
) -> list[EconomicEvent]:
    await init_db()
    start_ts = pd.Timestamp(start, tz="UTC").to_pydatetime()
    end_ts = pd.Timestamp(end, tz="UTC").to_pydatetime()
    async with get_session() as session:
        filters = [
            EconomicEvent.event_time >= start_ts,
            EconomicEvent.event_time <= end_ts,
            EconomicEvent.currency.in_(currencies),
            EconomicEvent.actual.isnot(None),
            EconomicEvent.forecast.isnot(None),
            EconomicEvent.impact.in_(impacts),
        ]
        if event_query:
            filters.append(EconomicEvent.event_name.ilike(f"%{event_query}%"))

        result = await session.execute(
            select(EconomicEvent)
            .where(and_(*filters))
            .order_by(EconomicEvent.event_time)
        )
        events = list(result.scalars().all())
    await close_db()
    return events


def _summarize_observations(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    grouped = df.groupby(["pair", "event_name", "horizon_hours", "surprise_bucket"])
    for (pair, event_name, horizon_hours, surprise_bucket), group in grouped:
        rets = group["ret_pips"].astype(float).values
        n = len(rets)
        mean_ret = float(np.mean(rets))
        median_ret = float(np.median(rets))
        std = float(np.std(rets, ddof=1)) if n > 1 else 0.0
        t_stat = mean_ret / (std / np.sqrt(n)) if n > 1 and std > 1e-12 else 0.0
        rows.append({
            "pair": pair,
            "event_name": event_name,
            "horizon_hours": horizon_hours,
            "surprise_bucket": surprise_bucket,
            "n_events": n,
            "mean_ret_pips": round(mean_ret, 2),
            "median_ret_pips": round(median_ret, 2),
            "win_rate_pos": round(float(np.mean(rets > 0)) * 100.0, 1),
            "t_stat": round(t_stat, 3),
            "mean_ret_pct": round(float(np.mean(group["ret_pct"].astype(float))), 4),
        })
    return pd.DataFrame(rows).sort_values(["pair", "event_name", "horizon_hours", "surprise_bucket"])


def _surprise_bucket(sign: float, magnitude: float | None, mag_threshold: float) -> str:
    if sign > 0:
        return "POS_BIG" if magnitude is not None and magnitude >= mag_threshold else "POS"
    if sign < 0:
        return "NEG_BIG" if magnitude is not None and magnitude >= mag_threshold else "NEG"
    return "FLAT"


async def _run(args) -> int:
    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    currencies = [c.strip().upper() for c in args.currencies.split(",") if c.strip()]
    impacts = [i.strip().upper() for i in args.impacts.split(",") if i.strip()]
    windows = [int(w.strip()) for w in args.windows.split(",") if w.strip()]

    h1_data = {}
    for pair in pairs:
        try:
            h1_data[pair] = _load_h1_csv(pair, Path(args.data_dir))
        except FileNotFoundError:
            print(f"SKIP: missing H1 CSV for {pair}")

    if not h1_data:
        print("ERROR: no H1 data loaded")
        return 1

    events = await _fetch_events(currencies, args.start, args.end, impacts, args.event_query)
    if not events:
        print("No matching released events found.")
        return 0

    observations: list[EventObservation] = []
    for event in events:
        sign = _surprise_sign(event.actual, event.forecast)
        if sign is None:
            continue
        actual_val = _parse_value(event.actual)
        forecast_val = _parse_value(event.forecast)
        magnitude = None
        if actual_val is not None and forecast_val is not None and abs(forecast_val) > 1e-12:
            magnitude = abs(actual_val - forecast_val) / abs(forecast_val)

        relevant_pairs = _relevant_pairs_for_currency(event.currency, list(h1_data.keys()))
        for pair in relevant_pairs:
            df_h1 = h1_data[pair]
            event_ts = _utc_timestamp(event.event_time)
            for horizon_hours in windows:
                out = _return_from_event(df_h1, pair, event.currency, event_ts, horizon_hours)
                if out is None:
                    continue
                exit_time, ret_pips, ret_pct, direction_tag = out
                observations.append(
                    EventObservation(
                        pair=pair,
                        currency=event.currency,
                        event_name=event.event_name,
                        event_time=event_ts,
                        trade_time=exit_time,
                        horizon_hours=horizon_hours,
                        direction_tag=direction_tag,
                        surprise_sign=sign,
                        surprise_mag=magnitude,
                        ret_pips=ret_pips,
                        ret_pct=ret_pct,
                    )
                )

    if not observations:
        print("No event observations could be aligned to H1 data.")
        return 0

    obs_df = pd.DataFrame([o.__dict__ for o in observations])
    obs_df["surprise_bucket"] = [
        _surprise_bucket(s, m, args.big_surprise_threshold)
        for s, m in zip(obs_df["surprise_sign"], obs_df["surprise_mag"], strict=False)
    ]

    summary = _summarize_observations(obs_df)

    print(f"\n{'='*88}")
    print("EVENT STUDY")
    print(f"{'='*88}")
    print(f"Pairs      : {', '.join(sorted(h1_data.keys()))}")
    print(f"Currencies : {', '.join(currencies)}")
    print(f"Impacts    : {', '.join(impacts)}")
    print(f"Query      : {args.event_query or 'ALL'}")
    print(f"Window hrs : {', '.join(str(w) for w in windows)}")
    print(f"Events     : {len(events)} raw / {len(obs_df)} aligned observations")
    print(f"{'='*88}")
    print(f"{'Pair':<10} {'Event':<28} {'H':>3} {'Bucket':<8} {'N':>4} {'MeanPips':>10} {'MedPips':>9} {'WR+':>6} {'t':>7}")
    for _, row in summary.iterrows():
        print(
            f"{row['pair']:<10} {str(row['event_name'])[:28]:<28} {int(row['horizon_hours']):>3} "
            f"{row['surprise_bucket']:<8} {int(row['n_events']):>4} {row['mean_ret_pips']:>10.2f} "
            f"{row['median_ret_pips']:>9.2f} {row['win_rate_pos']:>5.1f}% {row['t_stat']:>7.3f}"
        )
    print(f"{'='*88}\n")

    if args.export_csv:
        export_path = Path(args.export_csv)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        obs_df.to_csv(export_path.with_suffix(".observations.csv"), index=False)
        summary.to_csv(export_path.with_suffix(".summary.csv"), index=False)
        print(f"Saved: {export_path.with_suffix('.observations.csv')}")
        print(f"Saved: {export_path.with_suffix('.summary.csv')}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Generic FX macro event-study framework")
    parser.add_argument("--pairs", default="EUR_USD,GBP_USD,USD_JPY,USD_CAD")
    parser.add_argument("--currencies", default="USD,EUR,GBP,JPY,CAD")
    parser.add_argument("--impacts", default="HIGH")
    parser.add_argument("--event-query", default=None, help="Case-insensitive substring filter on event name")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--windows", default="1,4,8,24", help="Comma-separated post-event horizons in hours")
    parser.add_argument("--big-surprise-threshold", type=float, default=0.25, help="Relative surprise threshold for *_BIG buckets")
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--export-csv", default=None, help="Prefix path for observations/summary CSVs")
    raise_code = asyncio.run(_run(parser.parse_args()))
    raise SystemExit(raise_code)


if __name__ == "__main__":
    main()
