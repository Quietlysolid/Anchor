"""
Mean-Reversion Strategy Backtester.

Replays historical H1 candle data through MeanReversionEngine (same logic
used in live trading). Produces the same statistics as the trend backtest
so you can compare regime-split performance.

Usage:
    python -m anchor.backtesting.mr_backtest \\
        --instrument EUR_USD \\
        --h1-csv  data/EUR_USD_H1.csv \\
        --d-csv   data/EUR_USD_D.csv \\
        --balance 10000

    # All active pairs
    for pair in EUR_USD GBP_USD NZD_USD USD_CAD EUR_JPY AUD_USD; do
        python -m anchor.backtesting.mr_backtest \\
            --instrument $pair \\
            --h1-csv data/${pair}_H1.csv \\
            --d-csv  data/${pair}_D.csv
    done
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta, timezone

import numpy as np
import pandas as pd
import structlog

from anchor.signals.mean_reversion_engine import MeanReversionEngine, MRSignalResult
from anchor.utils.math_utils import get_pip_size

logger = structlog.get_logger(__name__)

ATR_PERIOD = 14

# Overnight swap cost in pips per OANDA 22:00 UTC rollover (positive = receive, negative = pay).
# London trades (07-12 UTC) mostly close before rollover but can carry overnight.
_SWAP_PIPS: dict[str, dict[str, float]] = {
    "EUR_USD": {"LONG": -0.4, "SHORT": +0.3},
    "GBP_USD": {"LONG": -0.4, "SHORT": +0.3},
    "USD_JPY": {"LONG": +0.8, "SHORT": -1.5},
    "AUD_USD": {"LONG": -0.5, "SHORT": +0.3},
    "USD_CAD": {"LONG": +0.3, "SHORT": -0.5},
    "EUR_JPY": {"LONG": -0.8, "SHORT": +0.5},
    "GBP_JPY": {"LONG": -0.8, "SHORT": +0.5},
    "NZD_USD": {"LONG": -0.4, "SHORT": +0.3},
    "USD_CHF": {"LONG": +0.4, "SHORT": -0.5},
}


def _compute_atr(df: pd.DataFrame, idx: int) -> float:
    end   = idx + 1
    start = max(0, end - ATR_PERIOD - 1)
    sub   = df.iloc[start:end]
    prev  = sub["close"].shift(1)
    tr = pd.concat([
        sub["high"] - sub["low"],
        (sub["high"] - prev).abs(),
        (sub["low"]  - prev).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(ATR_PERIOD).mean().iloc[-1])


def _load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"])
    df = df.set_index("time").sort_index()
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"])


def _limit_order_fill_price(direction: str, limit_price: float, bar: pd.Series) -> float | None:
    """Fill a pending MR limit order when price revisits the signal level."""
    if direction == "LONG":
        if float(bar["low"]) > limit_price:
            return None
        return min(limit_price, float(bar["open"]))

    if float(bar["high"]) < limit_price:
        return None
    return max(limit_price, float(bar["open"]))


class MRBacktestEngine:
    def __init__(self, initial_balance: float = 10_000.0):
        self.initial_balance = initial_balance

    def run(
        self,
        instrument: str,
        df_h1: pd.DataFrame,
        df_daily: pd.DataFrame | None = None,
        df_h4: pd.DataFrame | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict:
        """
        Run mean-reversion backtest bar by bar.
        Returns dict with trade list and summary statistics.
        """
        if start:
            df_h1 = df_h1[df_h1.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            df_h1 = df_h1[df_h1.index <= pd.Timestamp(end, tz="UTC")]

        if len(df_h1) < 60:
            return {"error": "insufficient_data", "trades": [], "stats": {}}

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self._run_async(instrument, df_h1, df_daily, df_h4)
            )
        finally:
            loop.close()

    async def _run_async(
        self,
        instrument: str,
        df_h1: pd.DataFrame,
        df_daily: pd.DataFrame | None,
        df_h4: pd.DataFrame | None = None,
    ) -> dict:
        WARMUP = 50  # bars needed for indicators
        balance = self.initial_balance
        trades: list[dict] = []

        # Simulate open position state
        position: dict | None = None
        pending_order: dict | None = None

        engine = MeanReversionEngine()

        for i in range(WARMUP, len(df_h1)):
            bar     = df_h1.iloc[i]
            dt      = bar.name
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            if pending_order is not None:
                if dt > pending_order["expires_at"]:
                    pending_order = None
                else:
                    fill_price = _limit_order_fill_price(pending_order["direction"], pending_order["entry"], bar)
                    if fill_price is not None:
                        position = {
                            "direction":     pending_order["direction"],
                            "entry":         fill_price,
                            "sl":            pending_order["sl"],
                            "tp":            pending_order["tp"],
                            "entry_time":    dt,
                            "risk_fraction": pending_order["risk_fraction"],
                            "confluence":    pending_order["confluence"],
                            "swap_pips":     0.0,
                        }
                        pending_order = None

            # ── Check if open position hit SL or TP ───────────────────────
            if position is not None:
                hi  = float(bar["high"])
                lo  = float(bar["low"])
                sl  = position["sl"]
                tp  = position["tp"]
                direction = position["direction"]

                closed = False
                if direction == "LONG":
                    if lo <= sl:
                        exit_price, close_reason = sl, "SL"
                        closed = True
                    elif hi >= tp:
                        exit_price, close_reason = tp, "TP"
                        closed = True
                else:
                    if hi >= sl:
                        exit_price, close_reason = sl, "SL"
                        closed = True
                    elif lo <= tp:
                        exit_price, close_reason = tp, "TP"
                        closed = True

                if closed:
                    pip     = get_pip_size(instrument)
                    dist    = abs(exit_price - position["entry"])
                    sl_dist = abs(position["sl"] - position["entry"])
                    pips    = dist / pip
                    sign    = 1 if (direction == "LONG") == (exit_price > position["entry"]) else -1
                    pl_pips = sign * pips
                    # Correct 1% fixed-fractional: win = +(dist/sl_dist)×1%, loss = -1×1%
                    pl_pct  = sign * (dist / sl_dist) * position["risk_fraction"]

                    # Apply swap/rollover cost for each 22:00 UTC boundary crossed
                    swap_net = position["swap_pips"]
                    if swap_net != 0.0:
                        sl_dist_pips = sl_dist / pip if pip > 0 else 1.0
                        swap_pct = (swap_net * position["risk_fraction"]) / sl_dist_pips
                        pl_pips += swap_net
                        pl_pct  += swap_pct

                    balance *= (1 + pl_pct)

                    trades.append({
                        "entry_time":  position["entry_time"].isoformat(),
                        "exit_time":   dt.isoformat(),
                        "direction":   direction,
                        "entry":       position["entry"],
                        "exit":        exit_price,
                        "sl":          sl,
                        "tp":          tp,
                        "reason":      close_reason,
                        "pl_pips":     round(pl_pips, 1),
                        "pl_pct":      round(pl_pct * 100, 3),
                        "swap_pips":   round(swap_net, 2),
                        "balance":     round(balance, 2),
                    })
                    position = None

                if not closed and dt.hour == 22:
                    # 22:00 UTC rollover — accrue overnight swap
                    pair_swap = _SWAP_PIPS.get(instrument, {})
                    position["swap_pips"] += pair_swap.get(direction, 0.0)

            if position is not None or pending_order is not None:
                continue  # one position at a time

            # ── Build data slice and run engine ───────────────────────────
            slice_h1 = df_h1.iloc[max(0, i - 199):i + 1]
            engine.data_cache = {"H1": {instrument: slice_h1}}

            if df_daily is not None:
                slice_d = df_daily[df_daily.index <= dt].tail(300)
                engine.data_cache["D"] = {instrument: slice_d}

            if df_h4 is not None:
                # Only include fully-closed H4 bars (each bar closes 4h after its open time).
                # This prevents look-ahead from the current incomplete H4 bar.
                slice_h4 = df_h4[df_h4.index + pd.Timedelta(hours=4) <= dt].tail(300)
                engine.data_cache["H4"] = {instrument: slice_h4}

            result: MRSignalResult = await engine.evaluate(instrument, dt=dt)

            if result.suppressed or result.direction is None:
                continue

            entry = result.entry_price or float(bar["close"])
            sl    = result.stop_loss
            tp    = result.take_profit

            if sl is None or tp is None:
                continue

            sl_dist = abs(entry - sl)
            if sl_dist < 1e-8:
                continue

            risk_fraction = 0.01  # 1% per trade
            pending_order = {
                "direction":     result.direction,
                "entry":         entry,
                "sl":            sl,
                "tp":            tp,
                "expires_at":    dt + timedelta(hours=2),
                "risk_fraction": risk_fraction,
                "confluence":    result.confluence_score,
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

        stats = {
            "n_trades":    len(trades),
            "win_rate":    round(len(wins) / len(trades) * 100, 1),
            "profit_factor": round(pf, 3),
            "net_pct":     round(net_pct, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "avg_win_pips":  round(float(np.mean([t["pl_pips"] for t in wins])), 1) if wins else 0,
            "avg_loss_pips": round(float(np.mean([t["pl_pips"] for t in losses])), 1) if losses else 0,
            "final_balance": round(balance, 2),
        }

        logger.info(
            "mr_backtest_complete",
            instrument=instrument,
            **stats,
        )
        return {"instrument": instrument, "trades": trades, "stats": stats}


def main():
    parser = argparse.ArgumentParser(description="Mean-reversion backtest")
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--h1-csv",  required=True)
    parser.add_argument("--d-csv",   default=None)
    parser.add_argument("--h4-csv",  default=None)
    parser.add_argument("--balance", type=float, default=10_000.0)
    parser.add_argument("--start",   default=None)
    parser.add_argument("--end",     default=None)
    args = parser.parse_args()

    df_h1    = _load_csv(args.h1_csv)
    df_daily = _load_csv(args.d_csv) if args.d_csv else None
    df_h4    = _load_csv(args.h4_csv) if args.h4_csv else None

    engine  = MRBacktestEngine(initial_balance=args.balance)
    results = engine.run(
        instrument=args.instrument,
        df_h1=df_h1,
        df_daily=df_daily,
        df_h4=df_h4,
        start=args.start,
        end=args.end,
    )

    stats = results.get("stats", {})
    print(f"\n{'='*50}")
    print(f"MR Backtest — {args.instrument}")
    print(f"{'='*50}")
    for k, v in stats.items():
        print(f"  {k:<25} {v}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
