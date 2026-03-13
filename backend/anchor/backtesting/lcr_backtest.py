"""
London Close Reversal (LCR) Backtester.

Replays historical H1 candle data through LondonCloseReversionEngine — the same
logic used in live trading. Produces WR, PF, monthly return, and drawdown stats.

Only fires on bars with open hour 17, 18, or 19 UTC (NY session window).
Allowed instruments: EUR_USD, GBP_USD, USD_JPY.

Usage:
    python -m anchor.backtesting.lcr_backtest \\
        --instrument EUR_USD \\
        --h1-csv  data/EUR_USD_H1.csv \\
        --balance 10000

    # All LCR pairs
    for pair in EUR_USD GBP_USD USD_JPY; do
        python -m anchor.backtesting.lcr_backtest \\
            --instrument $pair \\
            --h1-csv data/${pair}_H1.csv
    done
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from collections import defaultdict

import numpy as np
import pandas as pd
import structlog

from anchor.signals.london_close_reversion import LondonCloseReversionEngine, LCR_INSTRUMENTS
from anchor.utils.math_utils import get_pip_size

logger = structlog.get_logger(__name__)

# Bars of H1 history to feed into the engine per evaluation (LCR needs ~200 for ATR)
_LOOKBACK = 200
# Minimum H1 bars needed before we start evaluating
_WARMUP   = 50
# Typical round-trip spread cost per instrument (in price, not pips).
# Conservative estimate: slightly above normal spread to account for NY session widening.
# EUR_USD: 0.2 pip = 0.00002, GBP_USD: 0.4 pip = 0.00004, USD_JPY: 0.5 pip = 0.050
_SPREAD_COST = {
    "EUR_USD": 0.00002,
    "GBP_USD": 0.00004,
    "USD_JPY": 0.050,
}
# Minimum SL in pips — skip degenerate signals where entry ≈ SL (tiny ATR or bad alignment)
_MIN_SL_PIPS = 3
# Per-instrument LCR risk fraction (USD_JPY reduced due to higher backtest drawdown)
_LCR_RISK = {
    "EUR_USD": 0.01,
    "GBP_USD": 0.01,
    "USD_JPY": 0.005,   # 0.5% — -41% max DD at 1% is too high
}


def _load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"])
    df = df.set_index("time").sort_index()
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.index = df.index.tz_localize("UTC") if df.index.tzinfo is None else df.index.tz_convert("UTC")
    return df.dropna(subset=["open", "high", "low", "close"])


class LCRBacktestEngine:
    def __init__(self, initial_balance: float = 10_000.0):
        self.initial_balance = initial_balance

    def run(
        self,
        instrument: str,
        df_h1: pd.DataFrame,
        start: str | None = None,
        end: str | None = None,
    ) -> dict:
        if instrument not in LCR_INSTRUMENTS:
            return {"error": f"LCR not defined for {instrument}", "trades": [], "stats": {}}

        if start:
            df_h1 = df_h1[df_h1.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            df_h1 = df_h1[df_h1.index <= pd.Timestamp(end, tz="UTC")]

        if len(df_h1) < _WARMUP:
            return {"error": "insufficient_data", "trades": [], "stats": {}}

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._run_async(instrument, df_h1))
        finally:
            loop.close()

    async def _run_async(self, instrument: str, df_h1: pd.DataFrame) -> dict:
        balance  = self.initial_balance
        trades: list[dict] = []
        position: dict | None = None

        engine = LondonCloseReversionEngine()

        pip = get_pip_size(instrument)

        for i in range(_WARMUP, len(df_h1)):
            bar = df_h1.iloc[i]
            dt  = bar.name
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            # ── Check if open position hit SL or TP ───────────────────────
            if position is not None:
                hi        = float(bar["high"])
                lo        = float(bar["low"])
                sl        = position["sl"]
                tp        = position["tp"]
                direction = position["direction"]

                closed = False
                if direction == "LONG":
                    if lo <= sl:
                        exit_price, close_reason = sl, "SL"
                        closed = True
                    elif hi >= tp:
                        exit_price, close_reason = tp, "TP"
                        closed = True
                else:  # SHORT
                    if hi >= sl:
                        exit_price, close_reason = sl, "SL"
                        closed = True
                    elif lo <= tp:
                        exit_price, close_reason = tp, "TP"
                        closed = True

                if closed:
                    dist    = abs(exit_price - position["entry"])
                    sl_dist = abs(position["sl"] - position["entry"])
                    pips    = dist / pip
                    sign    = 1 if (direction == "LONG") == (exit_price > position["entry"]) else -1
                    pl_pips = sign * pips
                    # Correct 1% fixed-fractional: win = +(dist/sl_dist)×1%, loss = -1×1%
                    pl_pct  = sign * (dist / sl_dist) * position["risk_fraction"]
                    balance *= 1.0 + pl_pct

                    trades.append({
                        "entry_time": position["entry_time"].isoformat(),
                        "exit_time":  dt.isoformat(),
                        "direction":  direction,
                        "entry":      position["entry"],
                        "exit":       exit_price,
                        "sl":         sl,
                        "tp":         tp,
                        "reason":     close_reason,
                        "pl_pips":    round(pl_pips, 1),
                        "pl_pct":     round(pl_pct * 100, 3),
                        "balance":    round(balance, 2),
                        "confluence": position["confluence"],
                        "london_mid": position["london_mid"],
                    })
                    position = None

            if position is not None:
                continue  # one position at a time

            # ── Only evaluate NY LCR hours ────────────────────────────────
            if dt.hour not in {17, 18, 19}:
                continue

            # ── Feed rolling window to LCR engine ─────────────────────────
            slice_h1 = df_h1.iloc[max(0, i - _LOOKBACK + 1):i + 1]
            engine.update_cache(instrument, "H1", slice_h1)

            result = await engine.evaluate(instrument, dt=dt)

            if result.suppressed or result.direction is None:
                continue

            entry = result.entry_price
            sl    = result.stop_loss
            tp    = result.take_profit

            if entry is None or sl is None or tp is None:
                continue

            sl_dist = abs(entry - sl)
            if sl_dist < 1e-8:
                continue

            # Apply half-spread cost to entry (worst-case fill)
            spread = _SPREAD_COST.get(instrument, 0.0)
            if result.direction == "LONG":
                entry += spread / 2
            else:
                entry -= spread / 2

            # Re-check sl_dist with spread-adjusted entry; enforce minimum SL
            sl_dist = abs(entry - sl)
            if sl_dist < 1e-8 or sl_dist < pip * _MIN_SL_PIPS:
                continue

            position = {
                "direction":     result.direction,
                "entry":         entry,
                "sl":            sl,
                "tp":            tp,
                "entry_time":    dt,
                "risk_fraction": _LCR_RISK.get(instrument, 0.01),
                "confluence":    result.confluence_score,
                "london_mid":    result.london_mid,
            }

        # ── Summary statistics ─────────────────────────────────────────────
        if not trades:
            return {"instrument": instrument, "trades": [], "stats": {"n_trades": 0}}

        wins   = [t for t in trades if t["pl_pips"] > 0]
        losses = [t for t in trades if t["pl_pips"] <= 0]

        gross_profit = sum(t["pl_pips"] for t in wins)
        gross_loss   = abs(sum(t["pl_pips"] for t in losses))
        pf           = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        equity = np.array([self.initial_balance] + [t["balance"] for t in trades])
        peak   = np.maximum.accumulate(equity)
        dd     = (equity - peak) / peak
        max_dd = float(np.min(dd))

        net_pct = (balance - self.initial_balance) / self.initial_balance * 100

        # Monthly breakdown
        monthly: dict[str, list] = defaultdict(list)
        for t in trades:
            ym = t["entry_time"][:7]  # "YYYY-MM"
            monthly[ym].append(t["pl_pips"])

        monthly_pf_list = []
        for ym, pips in sorted(monthly.items()):
            w = [p for p in pips if p > 0]
            l = [p for p in pips if p <= 0]
            gp = sum(w); gl = abs(sum(l))
            monthly_pf_list.append(f"{ym}: {len(pips)} trades  WR={len(w)/len(pips)*100:.0f}%  PF={gp/gl:.2f}" if gl > 0 else f"{ym}: {len(pips)} trades  WR=100%  PF=∞")

        stats = {
            "n_trades":          len(trades),
            "win_rate":          round(len(wins) / len(trades) * 100, 1),
            "profit_factor":     round(pf, 3),
            "net_pct":           round(net_pct, 2),
            "max_drawdown_pct":  round(max_dd * 100, 2),
            "avg_win_pips":      round(float(np.mean([t["pl_pips"] for t in wins])), 1)   if wins   else 0,
            "avg_loss_pips":     round(float(np.mean([t["pl_pips"] for t in losses])), 1) if losses else 0,
            "trades_per_month":  round(len(trades) / max(1, len(monthly)), 1),
            "final_balance":     round(balance, 2),
            "monthly_breakdown": monthly_pf_list,
        }

        logger.info("lcr_backtest_complete", instrument=instrument, **{k: v for k, v in stats.items() if k != "monthly_breakdown"})
        return {"instrument": instrument, "trades": trades, "stats": stats}


def main():
    parser = argparse.ArgumentParser(description="London Close Reversal backtest")
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--h1-csv",  required=True)
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--start",   default=None)
    parser.add_argument("--end",     default=None)
    args = parser.parse_args()

    if args.instrument not in LCR_INSTRUMENTS:
        print(f"LCR only supports: {', '.join(sorted(LCR_INSTRUMENTS))}")
        return

    df_h1 = _load_csv(args.h1_csv)

    engine  = LCRBacktestEngine(initial_balance=args.balance)
    results = engine.run(
        instrument=args.instrument,
        df_h1=df_h1,
        start=args.start,
        end=args.end,
    )

    stats = results.get("stats", {})
    monthly = stats.pop("monthly_breakdown", [])

    print(f"\n{'='*55}")
    print(f"LCR Backtest — {args.instrument}")
    print(f"{'='*55}")
    for k, v in stats.items():
        print(f"  {k:<28} {v}")
    if monthly:
        print(f"\n  Monthly breakdown:")
        for line in monthly:
            print(f"    {line}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
