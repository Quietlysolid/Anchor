"""
London Close Reversal (LCR) Backtester.

Replays historical H1 candle data through LondonCloseReversionEngine — the same
logic used in live trading. Produces WR, PF, monthly return, and drawdown stats.

Only fires on bars with open hour 17, 18, or 19 UTC (NY session window).
Active instruments (see config.py): EUR_USD, GBP_USD, NZD_USD, USD_CAD, EUR_JPY, AUD_USD.
Spread/swap/risk dicts below also include decommissioned pairs for historical analysis.

Usage:
    python -m anchor.backtesting.lcr_backtest \\
        --instrument EUR_USD \\
        --h1-csv  data/EUR_USD_H1.csv \\
        --balance 10000

    # All active LCR pairs
    for pair in EUR_USD GBP_USD NZD_USD USD_CAD EUR_JPY AUD_USD; do
        python -m anchor.backtesting.lcr_backtest \\
            --instrument $pair \\
            --h1-csv data/${pair}_H1.csv
    done
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import timezone
from collections import defaultdict

import numpy as np
import pandas as pd
import structlog

from anchor.signals.london_close_reversion import LondonCloseReversionEngine
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
    "AUD_USD": 0.00012,   # ~1.2 pip NY session spread
    "USD_CAD": 0.00015,   # ~1.5 pip NY session spread
    "EUR_JPY": 0.030,     # ~3 pip NY session spread
    "GBP_JPY": 0.040,     # ~4 pip NY session spread — widest of the majors
    "NZD_USD": 0.00015,   # ~1.5 pip NY session spread
    "USD_CHF": 0.00010,   # ~1.0 pip NY session spread
}
# Minimum SL in pips — skip degenerate signals where entry ≈ SL (tiny ATR or bad alignment)
_MIN_SL_PIPS = 3
# Per-instrument LCR risk fraction (USD_JPY reduced due to higher backtest drawdown)
_LCR_RISK = {
    "EUR_USD": 0.01,
    "GBP_USD": 0.01,
    "USD_JPY": 0.005,   # 0.5% — -41% max DD at 1% is too high
    "AUD_USD": 0.01,
    "USD_CAD": 0.01,    # 1.0% — walk-forward confirmed 7/7 profitable, lowest DD (-15.2%)
    "EUR_JPY": 0.0075,  # 0.75% — wider spread, start conservative
    "GBP_JPY": 0.005,   # 0.5% — widest spread, most volatile
    "NZD_USD": 0.01,    # 1% — similar spread to AUD_USD, start standard
    "USD_CHF": 0.01,    # 1% — tight spread, standard risk
}
# Overnight swap/rollover cost in pips per OANDA day-end (22:00 UTC).
# Positive = receive swap, negative = pay swap.
# Conservative averages across the historical period (2016-2024).
# LCR trades (17-19 UTC entry) may cross 22:00 UTC rollover if not closed same day.
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
        if instrument not in _SPREAD_COST:
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

                    # Apply swap/rollover cost: each 22:00 UTC boundary crossed costs/earns pips
                    # swap_pips is net (positive=received, negative=paid)
                    swap_net = position["swap_pips"]
                    if swap_net != 0.0:
                        sl_dist_pips = sl_dist / pip if pip > 0 else 1.0
                        swap_pct = (swap_net * position["risk_fraction"]) / sl_dist_pips
                        pl_pips += swap_net
                        pl_pct  += swap_pct

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
                        "swap_pips":  round(swap_net, 2),
                        "balance":    round(balance, 2),
                        "confluence": position["confluence"],
                        "london_mid": position["london_mid"],
                    })
                    position = None

                if not closed and dt.hour == 22:
                    # 22:00 UTC rollover boundary — accrue swap cost for this overnight hold
                    pair_swap = _SWAP_PIPS.get(instrument, {})
                    position["swap_pips"] += pair_swap.get(direction, 0.0)

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
                "swap_pips":     0.0,  # accrued rollover cost; updated each 22:00 UTC bar
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
            winners = [p for p in pips if p > 0]
            losers = [p for p in pips if p <= 0]
            gp = sum(winners)
            gl = abs(sum(losers))
            monthly_pf_list.append(f"{ym}: {len(pips)} trades  WR={len(winners)/len(pips)*100:.0f}%  PF={gp/gl:.2f}" if gl > 0 else f"{ym}: {len(pips)} trades  WR=100%  PF=∞")

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

    _BACKTEST_INSTRUMENTS = set(_SPREAD_COST.keys())
    if args.instrument not in _BACKTEST_INSTRUMENTS:
        print(f"LCR backtest supports: {', '.join(sorted(_BACKTEST_INSTRUMENTS))}")
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
        print("\n  Monthly breakdown:")
        for line in monthly:
            print(f"    {line}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
