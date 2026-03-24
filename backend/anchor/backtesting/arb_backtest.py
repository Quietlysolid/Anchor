"""
Asian Range Breakout (ARB) backtester.

Implements the current standalone Asian breakout design:
1. Asian range = 23:00-06:00 UTC (previous day's 23:00 bar + today's 00:00-06:00 bars)
2. Entry = first H1 close after the range that closes through the range extreme
3. Stop = opposite side of the Asian range
4. Range quality filters = instrument-specific minimum / maximum Asian range
5. No HMM or live signal-engine dependency

This module is intentionally self-contained so the strategy can be validated
across the 5-pair research universe before any production wiring.

Usage:
    python -m anchor.backtesting.arb_backtest \
        --instrument EUR_USD \
        --h1-csv data/EUR_USD_H1.csv \
        --balance 10000

    for pair in EUR_USD GBP_USD USD_JPY AUD_USD USD_CAD; do
        python -m anchor.backtesting.arb_backtest \
            --instrument "$pair" \
            --h1-csv "data/${pair}_H1.csv"
    done
"""
from __future__ import annotations

import argparse
from collections import defaultdict

import numpy as np
import pandas as pd
import structlog

from anchor.utils.math_utils import get_pip_size

logger = structlog.get_logger(__name__)

_RESEARCH_PAIRS = {"EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD"}
_ASIAN_HOURS = {23, 0, 1, 2, 3, 4, 5, 6}
_ENTRY_HOURS = {7, 8, 9, 10, 11, 12}
_TIME_STOP_HOUR = 21
_MIN_SL_PIPS = 3.0
_RISK_PER_TRADE = 0.01
_DEFAULT_RR = 2.0

# Conservative round-trip spread assumptions in price terms.
_SPREAD_COST = {
    "EUR_USD": 0.00006,
    "GBP_USD": 0.00008,
    "USD_JPY": 0.060,
    "AUD_USD": 0.00008,
    "USD_CAD": 0.00010,
}

# Asian-session range quality guardrails in pips.
_MIN_RANGE_PIPS = {
    "EUR_USD": 12.0,
    "GBP_USD": 15.0,
    "USD_JPY": 12.0,
    "AUD_USD": 10.0,
    "USD_CAD": 10.0,
}
_MAX_RANGE_PIPS = {
    "EUR_USD": 60.0,
    "GBP_USD": 75.0,
    "USD_JPY": 60.0,
    "AUD_USD": 55.0,
    "USD_CAD": 55.0,
}


def _load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"])
    df = df.set_index("time").sort_index()
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df.dropna(subset=["open", "high", "low", "close"])


def _trade_day(ts: pd.Timestamp):
    return (ts + pd.Timedelta(hours=1)).date() if ts.hour == 23 else ts.date()


def _monthly_key(value: str) -> str:
    return value[:7]


class ARBBacktestEngine:
    def __init__(self, initial_balance: float = 10_000.0, rr_target: float | None = _DEFAULT_RR):
        self.initial_balance = initial_balance
        self.rr_target = rr_target

    def run(
        self,
        instrument: str,
        df_h1: pd.DataFrame,
        start: str | None = None,
        end: str | None = None,
    ) -> dict:
        if instrument not in _RESEARCH_PAIRS:
            return {"error": f"ARB not defined for {instrument}", "trades": [], "stats": {}}

        if start:
            df_h1 = df_h1[df_h1.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            df_h1 = df_h1[df_h1.index <= pd.Timestamp(end, tz="UTC")]
        if len(df_h1) < 100:
            return {"instrument": instrument, "trades": [], "stats": {"n_trades": 0}}

        pip = get_pip_size(instrument)
        spread = _SPREAD_COST[instrument]
        min_range = _MIN_RANGE_PIPS[instrument]
        max_range = _MAX_RANGE_PIPS[instrument]

        df = df_h1.copy()
        df["_trade_day"] = [_trade_day(ts) for ts in df.index]
        grouped = {day: grp.sort_index() for day, grp in df.groupby("_trade_day")}
        sorted_days = sorted(grouped.keys())

        balance = self.initial_balance
        trades: list[dict] = []
        rejected = defaultdict(int)

        for day in sorted_days:
            day_df = grouped[day]
            eval_dt = pd.Timestamp(day, tz="UTC")

            if eval_dt.weekday() >= 5:
                continue

            asian_bars = day_df[day_df.index.hour.isin(_ASIAN_HOURS)]
            if len(asian_bars) < 7:
                rejected["insufficient_asian_bars"] += 1
                continue

            asian_high = float(asian_bars["high"].max())
            asian_low = float(asian_bars["low"].min())
            range_pips = (asian_high - asian_low) / pip

            if range_pips < min_range:
                rejected["range_too_small"] += 1
                continue
            if range_pips > max_range:
                rejected["range_too_large"] += 1
                continue

            entry_bars = day_df[day_df.index.hour.isin(_ENTRY_HOURS)]
            if entry_bars.empty:
                rejected["no_entry_window"] += 1
                continue

            entry_time = None
            entry_price = None
            direction = None
            sl = None
            tp = None
            sl_dist = None
            rejected_for_small_sl = False

            for _, bar in entry_bars.iterrows():
                close_price = float(bar["close"])
                bar_time = bar.name

                if close_price > asian_high:
                    direction = "LONG"
                    entry_price = close_price + spread / 2
                    sl = asian_low
                    sl_dist = entry_price - sl
                    if sl_dist < pip * _MIN_SL_PIPS:
                        rejected["sl_too_small"] += 1
                        rejected_for_small_sl = True
                        direction = None
                        break
                    tp = entry_price + self.rr_target * sl_dist if self.rr_target is not None else None
                    entry_time = bar_time
                    break

                if close_price < asian_low:
                    direction = "SHORT"
                    entry_price = close_price - spread / 2
                    sl = asian_high
                    sl_dist = sl - entry_price
                    if sl_dist < pip * _MIN_SL_PIPS:
                        rejected["sl_too_small"] += 1
                        rejected_for_small_sl = True
                        direction = None
                        break
                    tp = entry_price - self.rr_target * sl_dist if self.rr_target is not None else None
                    entry_time = bar_time
                    break

            if direction is None or entry_time is None or entry_price is None or sl is None or sl_dist is None:
                if not rejected_for_small_sl:
                    rejected["no_breakout"] += 1
                continue

            tracking_bars = day_df[(day_df.index >= entry_time) & (day_df.index.hour <= _TIME_STOP_HOUR)].sort_index()
            if tracking_bars.empty:
                rejected["no_tracking_bars"] += 1
                continue

            exit_price = None
            exit_time = None
            exit_reason = None

            for _, tbar in tracking_bars.iterrows():
                tbar_time = tbar.name
                hi = float(tbar["high"])
                lo = float(tbar["low"])
                close = float(tbar["close"])

                if tbar_time == entry_time:
                    if tp is not None:
                        if direction == "LONG" and hi >= tp:
                            exit_price, exit_time, exit_reason = tp, tbar_time, "TP"
                            break
                        if direction == "SHORT" and lo <= tp:
                            exit_price, exit_time, exit_reason = tp, tbar_time, "TP"
                            break
                    continue

                if direction == "LONG":
                    if lo <= sl:
                        exit_price, exit_time, exit_reason = sl, tbar_time, "SL"
                        break
                    if tp is not None and hi >= tp:
                        exit_price, exit_time, exit_reason = tp, tbar_time, "TP"
                        break
                else:
                    if hi >= sl:
                        exit_price, exit_time, exit_reason = sl, tbar_time, "SL"
                        break
                    if tp is not None and lo <= tp:
                        exit_price, exit_time, exit_reason = tp, tbar_time, "TP"
                        break

                if tbar_time.hour == _TIME_STOP_HOUR:
                    exit_price, exit_time, exit_reason = close, tbar_time, "TIME"
                    break

            if exit_price is None:
                last_bar = tracking_bars.iloc[-1]
                exit_price = float(last_bar["close"])
                exit_time = last_bar.name
                exit_reason = "TIME_EOD"

            dist = abs(exit_price - entry_price)
            sign = 1 if (direction == "LONG") == (exit_price > entry_price) else -1
            pl_pips = sign * dist / pip
            pl_pct = sign * (dist / sl_dist) * _RISK_PER_TRADE
            balance *= 1.0 + pl_pct

            dp = 5 if pip < 0.005 else 3
            trades.append({
                "entry_time": entry_time.isoformat(),
                "exit_time": exit_time.isoformat(),
                "direction": direction,
                "entry": round(entry_price, dp),
                "exit": round(exit_price, dp),
                "sl": round(sl, dp),
                "tp": round(tp, dp) if tp is not None else None,
                "reason": exit_reason,
                "pl_pips": round(pl_pips, 1),
                "pl_pct": round(pl_pct * 100, 3),
                "balance": round(balance, 2),
                "asian_high": round(asian_high, dp),
                "asian_low": round(asian_low, dp),
                "asian_range_pips": round(range_pips, 1),
            })

        return self._build_result(instrument, trades, rejected, balance)

    def _build_result(
        self,
        instrument: str,
        trades: list[dict],
        rejected: dict[str, int],
        final_balance: float,
    ) -> dict:
        if not trades:
            return {
                "instrument": instrument,
                "trades": [],
                "stats": {"n_trades": 0},
                "rejected": dict(rejected),
            }

        wins = [t for t in trades if t["pl_pips"] > 0]
        losses = [t for t in trades if t["pl_pips"] <= 0]
        gross_profit = sum(t["pl_pips"] for t in wins)
        gross_loss = abs(sum(t["pl_pips"] for t in losses))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        equity = np.array([self.initial_balance] + [t["balance"] for t in trades])
        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak
        max_dd = float(np.min(drawdown))
        net_pct = (final_balance - self.initial_balance) / self.initial_balance * 100

        monthly: dict[str, list[float]] = defaultdict(list)
        for trade in trades:
            monthly[_monthly_key(trade["entry_time"])].append(trade["pl_pips"])

        monthly_lines = []
        for ym, pips in sorted(monthly.items()):
            winners = [p for p in pips if p > 0]
            losers = [p for p in pips if p <= 0]
            gp = sum(winners)
            gl = abs(sum(losers))
            monthly_pf = gp / gl if gl > 0 else float("inf")
            monthly_lines.append(
                f"{ym}: {len(pips)} trades  WR={len(winners)/len(pips)*100:.0f}%  "
                f"PF={monthly_pf:.2f}" if np.isfinite(monthly_pf)
                else f"{ym}: {len(pips)} trades  WR=100%  PF=∞"
            )

        stats = {
            "n_trades": len(trades),
            "win_rate": round(len(wins) / len(trades) * 100, 1),
            "profit_factor": round(pf, 3),
            "net_pct": round(net_pct, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "avg_win_pips": round(float(np.mean([t["pl_pips"] for t in wins])), 1) if wins else 0.0,
            "avg_loss_pips": round(float(np.mean([t["pl_pips"] for t in losses])), 1) if losses else 0.0,
            "trades_per_month": round(len(trades) / max(1, len(monthly)), 1),
            "final_balance": round(final_balance, 2),
            "monthly_breakdown": monthly_lines,
        }

        logger.info("arb_backtest_complete", instrument=instrument, **{k: v for k, v in stats.items() if k != "monthly_breakdown"})
        return {
            "instrument": instrument,
            "trades": trades,
            "stats": stats,
            "rejected": dict(rejected),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Asian Range Breakout backtest")
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--h1-csv", required=True)
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--rr", type=float, default=_DEFAULT_RR, help="Risk:reward target; set <= 0 for no fixed TP")
    args = parser.parse_args()

    if args.instrument not in _RESEARCH_PAIRS:
        print(f"ARB backtest supports: {', '.join(sorted(_RESEARCH_PAIRS))}")
        return

    rr_target = args.rr if args.rr > 0 else None
    df_h1 = _load_csv(args.h1_csv)
    engine = ARBBacktestEngine(initial_balance=args.balance, rr_target=rr_target)
    results = engine.run(
        instrument=args.instrument,
        df_h1=df_h1,
        start=args.start,
        end=args.end,
    )

    stats = dict(results.get("stats", {}))
    monthly = stats.pop("monthly_breakdown", [])
    rejected = results.get("rejected", {})

    print(f"\n{'='*55}")
    print(f"ARB Backtest — {args.instrument}")
    print(f"{'='*55}")
    for key, value in stats.items():
        print(f"  {key:<28} {value}")
    if rejected:
        print("\n  Rejected:")
        for key, value in sorted(rejected.items()):
            print(f"    {key:<24} {value}")
    if monthly:
        print("\n  Monthly breakdown:")
        for line in monthly:
            print(f"    {line}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
