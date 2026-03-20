"""
Support/Resistance Bounce Backtester.

Strategy:
  Price touches a key S/R level during London session (07:00–17:00 UTC).
  An H1 pin bar (rejection candle) forms at that level.
  Enter in the direction of the rejection.

  S/R levels used:
    - Prior day high/low  (most watched by institutions — primary)
    - Prior week high/low (5-day lookback — secondary)

  Scale-out exit:
    - Half position closes at 1.5:1 R:R → stop moves to breakeven
    - Remaining half targets next opposing S/R level (default 2.5:1 if none found)

  SL placement: 0.4×ATR beyond the touched level.
  Entry filters:
    - Pin bar wick must be ≥ 3× candle body (strong rejection only)
    - Price within 0.20×ATR of level
    - Candle close must be in the rejection half of the bar

Usage:
    python -m anchor.backtesting.sr_bounce_backtest \\
        --instrument EUR_USD \\
        --h1-csv data/EUR_USD_H1.csv \\
        --balance 10000
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import timezone

import numpy as np
import pandas as pd
import structlog

from anchor.utils.math_utils import get_pip_size

logger = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_WARMUP          = 200    # H1 bars before we start evaluating
_RISK_FRACTION   = 0.01   # 1% risk per trade
_PROXIMITY_ATR   = 0.20   # price must be within 0.20×ATR of a level to trigger
_SL_BUFFER_ATR   = 0.40   # SL placed 0.40×ATR beyond the touched level (more room)
_PIN_BAR_RATIO   = 3.0    # rejection wick must be ≥ 3× body (strong rejection only)
_MIN_SL_PIPS     = 3      # minimum SL distance in pips (skip degenerate signals)
_PARTIAL_TP_RR   = 1.5    # first partial close R:R
_DEFAULT_TP_RR   = 2.5    # second half R:R if no next S/R level found

# London session: bars opening 07:00–16:00 UTC
_LONDON_HOURS = set(range(7, 17))

_SPREAD_COST = {
    "EUR_USD": 0.00002,
    "GBP_USD": 0.00004,
    "AUD_USD": 0.00012,
    "USD_CAD": 0.00015,
    "EUR_JPY": 0.030,
    "NZD_USD": 0.00015,
    "USD_JPY": 0.050,
    "USD_CHF": 0.00010,
    "GBP_JPY": 0.040,
}

_OHLC_AGG = {"open": "first", "high": "max", "low": "min", "close": "last"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"])
    df = df.set_index("time").sort_index()
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.index = df.index.tz_localize("UTC") if df.index.tzinfo is None else df.index.tz_convert("UTC")
    return df.dropna(subset=["open", "high", "low", "close"])


def _compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return float(df["high"].max() - df["low"].min())
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    prev_c = np.roll(closes, 1)
    prev_c[0] = closes[0]
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_c), np.abs(lows - prev_c)))
    atr = float(np.mean(tr[:period]))
    alpha = 1.0 / period
    for tv in tr[period:]:
        atr = alpha * float(tv) + (1.0 - alpha) * atr
    return atr


def _detect_levels(
    d1_completed: pd.DataFrame,
) -> list[tuple[float, str]]:
    """
    Returns list of (price, type) where type is 'resistance' or 'support'.
    Only prior-day and prior-week H/L — the levels every institution watches.
    Uses only completed bars — no look-ahead.
    """
    levels: list[tuple[float, str]] = []

    # Prior day high/low (primary — most self-fulfilling in FX)
    if len(d1_completed) >= 1:
        prior = d1_completed.iloc[-1]
        levels.append((float(prior["high"]), "resistance"))
        levels.append((float(prior["low"]),  "support"))

    # Prior week high/low (last 5 completed D1 bars)
    if len(d1_completed) >= 5:
        week     = d1_completed.iloc[-5:]
        week_hi  = float(week["high"].max())
        week_lo  = float(week["low"].min())
        prior_hi = float(d1_completed.iloc[-1]["high"])
        prior_lo = float(d1_completed.iloc[-1]["low"])
        # Only add weekly level if it's distinct from prior-day (avoid duplicates)
        if abs(week_hi - prior_hi) > 0.0001:
            levels.append((week_hi, "resistance"))
        if abs(week_lo - prior_lo) > 0.0001:
            levels.append((week_lo, "support"))

    return levels


def _find_second_tp(
    levels: list[tuple[float, str]],  # noqa: E501
    entry: float,
    direction: str,
    sl_dist: float,
) -> float:
    """Next opposing S/R level beyond partial TP distance, or default 2.5:1."""
    min_dist = _PARTIAL_TP_RR * sl_dist
    if direction == "SHORT":
        candidates = [p for p, t in levels if t == "support" and entry - p > min_dist]
        if candidates:
            return max(candidates)   # nearest support below
        return entry - _DEFAULT_TP_RR * sl_dist
    else:
        candidates = [p for p, t in levels if t == "resistance" and p - entry > min_dist]
        if candidates:
            return min(candidates)   # nearest resistance above
        return entry + _DEFAULT_TP_RR * sl_dist


# ── Backtester ────────────────────────────────────────────────────────────────

class SRBounceBacktestEngine:
    def __init__(self, initial_balance: float = 10_000.0):
        self.initial_balance = initial_balance

    def run(
        self,
        instrument: str,
        df_h1: pd.DataFrame,
        start: str | None = None,
        end: str | None = None,
    ) -> dict:
        if start:
            df_h1 = df_h1[df_h1.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            df_h1 = df_h1[df_h1.index <= pd.Timestamp(end, tz="UTC")]

        if len(df_h1) < _WARMUP:
            return {"error": "insufficient_data", "trades": [], "stats": {}}

        pip    = get_pip_size(instrument)
        spread = _SPREAD_COST.get(instrument, 0.00015)
        dp     = 3 if instrument.endswith("JPY") or instrument.startswith("JPY") else 5

        # Pre-compute D1 from H1 — avoids resampling in the inner loop
        df_d1 = df_h1.resample("D").agg(_OHLC_AGG).dropna()

        balance  = self.initial_balance
        trades:  list[dict] = []
        position: dict | None = None

        for i in range(_WARMUP, len(df_h1)):
            bar = df_h1.iloc[i]
            dt  = bar.name
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            # ── Manage open position ────────────────────────────────────────
            if position is not None:
                hi      = float(bar["high"])
                lo      = float(bar["low"])
                d       = position["direction"]
                entry   = position["entry"]
                sl_orig = position["sl_original"]
                sl_dist = abs(sl_orig - entry)

                closed = False

                # Check partial TP (scale-out: first half at 1.5:1)
                if not position["half_closed"] and position["partial_tp"] is not None:
                    ptp = position["partial_tp"]
                    hit = (d == "LONG" and hi >= ptp) or (d == "SHORT" and lo <= ptp)
                    if hit:
                        pd_dist = abs(ptp - entry)
                        position["partial_pl_pct"] = 0.5 * (pd_dist / sl_dist) * _RISK_FRACTION
                        position["partial_pips"]   = 0.5 * pd_dist / pip
                        position["half_closed"]    = True
                        position["sl"]             = entry  # stop moves to breakeven

                sl = position["sl"]
                tp = position["tp"]

                if d == "LONG":
                    if lo <= sl:
                        exit_price, close_reason = sl, ("BE" if position["half_closed"] else "SL")
                        closed = True
                    elif hi >= tp:
                        exit_price, close_reason = tp, "TP"
                        closed = True
                else:  # SHORT
                    if hi >= sl:
                        exit_price, close_reason = sl, ("BE" if position["half_closed"] else "SL")
                        closed = True
                    elif lo <= tp:
                        exit_price, close_reason = tp, "TP"
                        closed = True

                if closed:
                    dist = abs(exit_price - entry)
                    sign = 1 if (d == "LONG") == (exit_price > entry) else -1

                    if position["half_closed"]:
                        if close_reason == "BE":
                            second_pct  = 0.0
                            second_pips = 0.0
                        else:
                            second_pct  = sign * 0.5 * (dist / sl_dist) * _RISK_FRACTION
                            second_pips = sign * 0.5 * dist / pip
                        pl_pct  = position["partial_pl_pct"] + second_pct
                        pl_pips = position["partial_pips"]   + second_pips
                        final_reason = f"{close_reason}_SCALE"
                    else:
                        pl_pct  = sign * (dist / sl_dist) * _RISK_FRACTION
                        pl_pips = sign * dist / pip
                        final_reason = close_reason

                    balance *= 1.0 + pl_pct

                    trades.append({
                        "entry_time":  position["entry_time"].isoformat(),
                        "exit_time":   dt.isoformat(),
                        "direction":   d,
                        "entry":       round(entry, dp),
                        "exit":        round(exit_price, dp),
                        "sl":          round(sl_orig, dp),
                        "tp":          round(tp, dp),
                        "reason":      final_reason,
                        "pl_pips":     round(pl_pips, 1),
                        "pl_pct":      round(pl_pct * 100, 3),
                        "balance":     round(balance, 2),
                        "level":       round(position["level"], dp),
                        "level_type":  position["level_type"],
                    })
                    position = None

            if position is not None:
                continue  # one position at a time

            # ── Session filter: London only ─────────────────────────────────
            if dt.hour not in _LONDON_HOURS:
                continue
            if dt.weekday() == 4 and dt.hour >= 16:
                continue  # Friday after 16:00 UTC — avoid weekend gap risk

            # ── ATR from recent H1 bars ─────────────────────────────────────
            h1_window = df_h1.iloc[max(0, i - 50):i]
            if len(h1_window) < 14:
                continue
            atr = _compute_atr(h1_window)
            if atr <= 0:
                continue

            # ── Detect S/R levels from completed D1 bars ───────────────────
            d1_up_to = df_d1[df_d1.index.normalize() < pd.Timestamp(dt.date(), tz="UTC")]
            if len(d1_up_to) < 2:
                continue
            levels = _detect_levels(d1_up_to)
            if not levels:
                continue

            # ── Current bar ─────────────────────────────────────────────────
            c_close = float(bar["close"])
            c_high  = float(bar["high"])
            c_low   = float(bar["low"])
            c_open  = float(bar["open"])
            body    = abs(c_close - c_open)
            min_body = pip  # avoid dividing by near-zero on doji candles

            # ── Check each level for proximity + pin bar ────────────────────
            for level_price, level_type in levels:
                distance = abs(c_close - level_price)
                if distance > _PROXIMITY_ATR * atr:
                    continue

                bar_range = c_high - c_low
                if level_type == "resistance":
                    wick = c_high - max(c_close, c_open)         # upper wick
                    if wick < _PIN_BAR_RATIO * max(body, min_body):
                        continue
                    if c_close >= level_price:
                        continue  # must close below resistance
                    # Close must be in the lower half of the bar (clean rejection)
                    if bar_range > 0 and c_close > c_low + 0.5 * bar_range:
                        continue
                    direction = "SHORT"
                else:
                    wick = min(c_close, c_open) - c_low          # lower wick
                    if wick < _PIN_BAR_RATIO * max(body, min_body):
                        continue
                    if c_close <= level_price:
                        continue  # must close above support
                    # Close must be in the upper half of the bar (clean rejection)
                    if bar_range > 0 and c_close < c_high - 0.5 * bar_range:
                        continue
                    direction = "LONG"

                # Entry price (spread-adjusted worst-case fill)
                entry = c_close + spread / 2 if direction == "LONG" else c_close - spread / 2

                # SL: 0.3×ATR beyond the level
                sl = (level_price + _SL_BUFFER_ATR * atr) if direction == "SHORT" else (level_price - _SL_BUFFER_ATR * atr)
                sl_dist = abs(entry - sl)
                if sl_dist < pip * _MIN_SL_PIPS:
                    continue

                # Partial TP at 1.5:1
                if direction == "SHORT":
                    partial_tp_raw = entry - _PARTIAL_TP_RR * sl_dist
                    partial_tp = partial_tp_raw if partial_tp_raw > (entry - _DEFAULT_TP_RR * sl_dist) else None
                else:
                    partial_tp_raw = entry + _PARTIAL_TP_RR * sl_dist
                    partial_tp = partial_tp_raw if partial_tp_raw < (entry + _DEFAULT_TP_RR * sl_dist) else None

                # Second TP: next S/R level in trade direction
                tp = _find_second_tp(levels, entry, direction, sl_dist)

                position = {
                    "direction":      direction,
                    "entry":          entry,
                    "sl":             sl,
                    "sl_original":    sl,
                    "tp":             tp,
                    "partial_tp":     partial_tp,
                    "half_closed":    False,
                    "partial_pl_pct": 0.0,
                    "partial_pips":   0.0,
                    "entry_time":     dt,
                    "level":          level_price,
                    "level_type":     level_type,
                }
                break  # one trade at a time — first valid level wins

        # ── Summary statistics ──────────────────────────────────────────────
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

        monthly: dict[str, list] = defaultdict(list)
        for t in trades:
            ym = t["entry_time"][:7]
            monthly[ym].append(t["pl_pips"])

        monthly_lines = []
        for ym, pips in sorted(monthly.items()):
            w  = [p for p in pips if p > 0]
            lo = [p for p in pips if p <= 0]
            gp = sum(w)
            gl = abs(sum(lo))
            monthly_lines.append(
                f"{ym}: {len(pips)} trades  WR={len(w)/len(pips)*100:.0f}%  PF={gp/gl:.2f}"
                if gl > 0 else
                f"{ym}: {len(pips)} trades  WR=100%  PF=∞"
            )

        stats = {
            "n_trades":          len(trades),
            "win_rate":          round(len(wins) / len(trades) * 100, 1),
            "profit_factor":     round(pf, 3),
            "net_pct":           round(net_pct, 2),
            "max_drawdown_pct":  round(max_dd * 100, 2),
            "avg_win_pips":      round(float(np.mean([t["pl_pips"] for t in wins])),   1) if wins   else 0,
            "avg_loss_pips":     round(float(np.mean([t["pl_pips"] for t in losses])), 1) if losses else 0,
            "trades_per_month":  round(len(trades) / max(1, len(monthly)), 1),
            "final_balance":     round(balance, 2),
            "monthly_breakdown": monthly_lines,
        }

        logger.info(
            "sr_bounce_backtest_complete",
            instrument=instrument,
            **{k: v for k, v in stats.items() if k != "monthly_breakdown"},
        )
        return {"instrument": instrument, "trades": trades, "stats": stats}


def main():
    parser = argparse.ArgumentParser(description="S/R Bounce backtest with scale-out")
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--h1-csv",     required=True)
    parser.add_argument("--balance",    type=float, default=10_000.0)
    parser.add_argument("--start",      default=None)
    parser.add_argument("--end",        default=None)
    args = parser.parse_args()

    df_h1 = _load_csv(args.h1_csv)

    engine  = SRBounceBacktestEngine(initial_balance=args.balance)
    results = engine.run(
        instrument=args.instrument,
        df_h1=df_h1,
        start=args.start,
        end=args.end,
    )

    stats   = results.get("stats", {})
    monthly = stats.pop("monthly_breakdown", [])

    print(f"\n{'='*55}")
    print(f"S/R Bounce Backtest — {args.instrument}")
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
