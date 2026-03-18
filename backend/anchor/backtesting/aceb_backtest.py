"""
Asian Compression-Expansion Breakout (ACEB) Backtester.

ARCHIVED — 2026-03-13
──────────────────────
This strategy was designed from first principles using Andersen et al. (2003),
Crabel (1990), and Moskowitz et al. (2012), then backtested against 8 years of
H1 data across EUR_USD, GBP_USD, USD_JPY.

Statistical outcome: FAILED validation.
  - t-statistic < 2.0 in all configurations (no statistical significance)
  - Permutation test p > 0.05 in all configurations
  - IS→OOS degradation inconsistent (OOS sometimes > IS = IS itself was noise)

Root cause: the 5-hour time window (07:00–12:00 UTC) cannot consistently
deliver a 2:1 R:R against a full-range stop. Realized R:R collapses to ~1:1,
which at 47–52% WR is negative or break-even EV.

What survived:
  1. EUR_USD SHORT at London open with NR4 compression: ~65% WR combined IS/OOS
     → Integrated into ConfluenceEngine as _nr4_compression() metadata flag.
  2. 12-week TSMOM direction gate: statistically robust, Tier-1 research
     → Integrated into ConfluenceEngine as _tsmom_direction() hard gate.

Do NOT re-open this strategy without a new hypothesis and new data.
See git log and engine.py for the two findings that were productionised.

──────────────────────

Mathematical basis
──────────────────
Andersen, Bollerslev, Diebold & Labys (2003, Econometrica) document that FX
intraday volatility follows a deterministic U-shaped seasonal: the trough is
the Asian session (00:00–06:00 UTC), the first spike is London open
(07:00–09:00 UTC), with a 2.5–3.5× volatility ratio between them.

Bollerslev (1986, JoE) GARCH(1,1) proves variance is mean-reverting; for FX
the reversion from the Asian trough occurs at London open because institutional
order flow (London banks) is calendrically predictable. This is the structural
mechanism being traded — not a technical pattern.

Strategy rules
──────────────
1. Asian range: high/low of H1 bars 00:00–05:00 UTC (6 bars)
2. Entry: H1 bar 07:00–09:00 UTC closes above asian_high + 0.25×ATR14 (LONG)
          or below asian_low − 0.25×ATR14 (SHORT) — Crabel's "stretch" filter
3. SL:   Opposite side of Asian range ± 0.25×ATR14 beyond the extreme
4. TP:   2:1 R:R from entry (Kelly-optimal at 55% WR)
5. Time stop: exit at close of 11:00 bar (= 12:00 UTC) if not already closed

Filters — all parameters from published sources, not optimized on this data
───────────────────────────────────────────────────────────────────────────
- Range quality: instrument-specific pip min/max (Crabel 1990)
- NR4: today's Asian range ≤ min of prior 3 days (Crabel 1990, +3–5% WR)
- TSMOM: 12-week sign-of-return direction filter (Moskowitz, Ooi & Pedersen
  2012, JFE) — only trade in trend direction
- Round-number SL adjustment: Osler (2003, JF) — stop clusters at X.XX00/50

Position sizing
───────────────
- 1% risk on normal days, 1.25% on NR4 days (confirmed compression premium)
- Optional volatility-normalized sizing (Barroso & Santa-Clara 2015, JFE):
  scale base risk by (target_vol / realized_vol_21d)

Statistical validation
──────────────────────
- t-statistic of mean trade return (must be > 2.0 for significance)
- Permutation test (--permtest): 10 000 shuffles, empirical p-value
- In-sample / out-of-sample split (--split YYYY-MM-DD)

Usage
─────
    python -m anchor.backtesting.aceb_backtest \\
        --instrument EUR_USD \\
        --h1-csv data/EUR_USD_H1.csv \\
        --d-csv  data/EUR_USD_D.csv \\
        --balance 10000 \\
        --split 2023-01-01

    # All 3 pairs
    for pair in EUR_USD GBP_USD USD_JPY; do
        python -m anchor.backtesting.aceb_backtest \\
            --instrument $pair \\
            --h1-csv data/${pair}_H1.csv \\
            --d-csv  data/${pair}_D.csv \\
            --split 2023-01-01 \\
            --permtest
    done
"""
from __future__ import annotations

import argparse
import logging
import math
from collections import defaultdict

import numpy as np
import pandas as pd
from anchor.utils.math_utils import (
    get_pip_size,
    wilder_atr_scalar,
)

# Silence structlog startup noise during backtest runs
logging.disable(logging.CRITICAL)

# ── Strategy constants (all sourced from published research) ──────────────────

# Session windows
_ASIAN_HOURS    = set(range(0, 6))    # 00:00–05:00 UTC (6 H1 bars, Tokyo core)
_ENTRY_HOURS    = {7, 8, 9}           # London open breakout window (3 bars)
_TIME_STOP_HOUR = 11                  # Close of 11:00 bar = time stop at 12:00 UTC

# ATR parameters
_ATR_PERIOD      = 14                 # Wilder's 14-period ATR (Wilder 1978)
_ATR_LOOKBACK    = 60                 # H1 bars before Asian session for ATR calculation
_ENTRY_MULT      = 0.25               # Entry buffer: 0.25×ATR above/below range (Crabel 1990)
_SL_MULT         = 0.25               # SL buffer: 0.25×ATR beyond opposite extreme

# Range quality filters — minimum/maximum pip range (Crabel 1990 practitioner thresholds)
# If range < min: no real compression (holiday / thin session)
# If range > max: already volatile, no compression to break from
_MIN_RANGE_PIPS: dict[str, float] = {"EUR_USD": 15, "GBP_USD": 20, "USD_JPY": 12}
_MAX_RANGE_PIPS: dict[str, float] = {"EUR_USD": 55, "GBP_USD": 65, "USD_JPY": 50}

# NR4: today's Asian range must be ≤ min of previous 3 days' Asian ranges
# (Crabel 1990 — identifies the best compression setups, +3–5% WR improvement)
_NR4_LOOKBACK    = 3

# TSMOM: 12-week (84 calendar day) return sign (Moskowitz, Ooi & Pedersen 2012)
# Only trade in the direction of the 12-week trend
# Neutral zone ±0.2%: too small a return = no directional conviction → bidirectional
_TSMOM_DAYS      = 84
_TSMOM_THRESHOLD = 0.002

# Position sizing (Kelly-derived)
_BASE_RISK       = 0.01               # 1% risk on normal NR days
_NR4_RISK        = 0.0125             # 1.25% on NR4 days (confirmed compression)
_MIN_SL_PIPS     = 3                  # Degenerate signal guard (same as LCR/MR)

# R:R target (Kelly-optimal at 55% WR is ≥1.8:1; we use 2.0:1)
_RR_TARGET       = 2.0

# Round-number SL adjustment (Osler 2003, JF)
# Stop-loss clusters at X.XX00 and X.XX50 (every 50 pips)
# Push SL 3 pips beyond the round number if within 3 pips of one
_ROUND_NUM_SL_BUFFER_PIPS = 3

# Conservative spread costs (London open; slightly wider than mid-session)
# Full round-trip spread; half applied at entry (same convention as LCR backtester)
_SPREAD_COST: dict[str, float] = {
    "EUR_USD": 0.00008,   # 0.8 pip
    "GBP_USD": 0.00010,   # 1.0 pip
    "USD_JPY": 0.080,     # 0.8 pip in price terms
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"])
    df = df.set_index("time").sort_index()
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df.dropna(subset=["open", "high", "low", "close"])


def _adjust_sl_round_number(sl: float, direction: str, pip: float) -> float:
    """Osler (2003): push SL beyond round number (X.XX00 or X.XX50) if within
    _ROUND_NUM_SL_BUFFER_PIPS pips. Avoids stop-hunt through figure levels."""
    step   = 50 * pip        # round numbers every 50 pips
    buffer = _ROUND_NUM_SL_BUFFER_PIPS * pip
    nearest = round(sl / step) * step
    if abs(sl - nearest) <= buffer:
        return (nearest - buffer) if direction == "LONG" else (nearest + buffer)
    return sl


def _tsmom_signal(daily_closes: pd.Series, eval_date: pd.Timestamp) -> str:
    """
    Moskowitz, Ooi & Pedersen (2012) 12-week TSMOM signal.
    Returns 'LONG', 'SHORT', or 'BOTH' (neutral zone ±0.2%).
    Uses daily close data up to (not including) eval_date.
    """
    past = daily_closes[daily_closes.index < eval_date].dropna()
    if len(past) < 2:
        return "BOTH"
    cutoff = eval_date - pd.Timedelta(days=_TSMOM_DAYS)
    prior  = past[past.index <= cutoff]
    if prior.empty:
        return "BOTH"
    close_now  = float(past.iloc[-1])
    close_past = float(prior.iloc[-1])
    if close_past <= 0:
        return "BOTH"
    ret = (close_now - close_past) / close_past
    if ret > _TSMOM_THRESHOLD:
        return "LONG"
    if ret < -_TSMOM_THRESHOLD:
        return "SHORT"
    return "BOTH"


def _permutation_test(pl_pct: list[float], n_perms: int = 10_000) -> tuple[float, float]:
    """
    Permutation test for edge significance.
    Shuffle trade returns n_perms times; compute fraction where
    shuffled mean ≥ actual mean (empirical p-value).
    Returns (actual_mean_pct, p_value).
    """
    arr   = np.array(pl_pct)
    actual_mean = float(np.mean(arr))
    count = 0
    rng   = np.random.default_rng(42)
    for _ in range(n_perms):
        shuffled = rng.permutation(arr)
        if float(np.mean(shuffled)) >= actual_mean:
            count += 1
    return actual_mean, count / n_perms


def _t_statistic(pl_pct: list[float]) -> float:
    """t-stat for H0: mean(pl_pct) = 0. t > 2.0 = statistically significant."""
    arr = np.array(pl_pct)
    n   = len(arr)
    if n < 2:
        return 0.0
    return float(np.mean(arr)) / (float(np.std(arr, ddof=1)) / math.sqrt(n))


def _build_daily_closes(df_h1: pd.DataFrame, df_daily: pd.DataFrame | None) -> pd.Series:
    """
    Build a daily close series for TSMOM.
    Uses df_daily if provided; otherwise resamples H1 to daily
    (last available H1 close per day = proxy for daily close).
    """
    if df_daily is not None and not df_daily.empty:
        s = df_daily["close"].copy()
        s.index = s.index.normalize()
        return s
    # Proxy: take last H1 bar close for each calendar day
    daily = df_h1["close"].resample("1D").last().dropna()
    daily.index = daily.index.normalize()
    return daily


def _monthly_key(dt) -> str:
    if hasattr(dt, "isoformat"):
        return dt.isoformat()[:7]
    return str(dt)[:7]


# ── Core engine ───────────────────────────────────────────────────────────────

class ACEBBacktestEngine:
    """
    Asian Compression-Expansion Breakout backtester.

    Implements the full ACEB strategy from first principles with all
    research-specified filters. No engine dependency — standalone.
    """

    def __init__(
        self,
        initial_balance: float = 10_000.0,
        use_nr4: bool = True,
        use_tsmom: bool = True,
        rr_target: float = _RR_TARGET,
        vol_target: float | None = None,   # e.g. 0.08 for vol-normalised sizing
    ):
        self.initial_balance = initial_balance
        self.use_nr4         = use_nr4
        self.use_tsmom       = use_tsmom
        self.rr_target       = rr_target
        self.vol_target      = vol_target  # None = fixed fractional only

    def run(
        self,
        instrument: str,
        df_h1: pd.DataFrame,
        df_daily: pd.DataFrame | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict:
        if start:
            df_h1 = df_h1[df_h1.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            df_h1 = df_h1[df_h1.index <= pd.Timestamp(end, tz="UTC")]
        if len(df_h1) < 100:
            return {"instrument": instrument, "trades": [], "stats": {"n_trades": 0}}

        pip     = get_pip_size(instrument)
        spread  = _SPREAD_COST.get(instrument, 0.0001)
        min_pip = _MIN_RANGE_PIPS.get(instrument, 15)
        max_pip = _MAX_RANGE_PIPS.get(instrument, 60)

        daily_closes = _build_daily_closes(df_h1, df_daily)

        # Pre-index: group H1 bars by UTC date for fast daily lookup
        df_h1_copy        = df_h1.copy()
        df_h1_copy["_date"] = df_h1_copy.index.date
        grouped           = {d: grp for d, grp in df_h1_copy.groupby("_date")}
        sorted_dates      = sorted(grouped.keys())

        balance  = self.initial_balance
        trades: list[dict] = []
        asian_range_history: list[float] = []  # for NR4 — rolling last 3 days

        # Rejection counters for each filter
        rejected = defaultdict(int)

        # 21-day vol for optional vol targeting (Barroso & Santa-Clara 2015)
        daily_returns: list[float] = []

        for d_idx, today in enumerate(sorted_dates):
            day_df  = grouped[today]
            eval_dt = pd.Timestamp(today, tz="UTC")

            # ── Weekend guard ────────────────────────────────────────────
            if eval_dt.weekday() >= 5:
                continue

            # ── Build Asian session bars (00:00–05:00 UTC) ───────────────
            asian_bars = day_df[day_df.index.hour.isin(_ASIAN_HOURS)]
            if len(asian_bars) < 4:
                rejected["insufficient_asian_bars"] += 1
                continue

            asian_high  = float(asian_bars["high"].max())
            asian_low   = float(asian_bars["low"].min())
            asian_range = asian_high - asian_low

            # ── Range quality filter (Crabel 1990) ───────────────────────
            range_pips = asian_range / pip
            if range_pips < min_pip:
                rejected["range_too_small"] += 1
                asian_range_history.append(asian_range)
                continue
            if range_pips > max_pip:
                rejected["range_too_large"] += 1
                asian_range_history.append(asian_range)
                continue

            # ── NR4 filter (Crabel 1990) ─────────────────────────────────
            is_nr4 = False
            if self.use_nr4 and len(asian_range_history) >= _NR4_LOOKBACK:
                prior_ranges = asian_range_history[-_NR4_LOOKBACK:]
                is_nr4 = asian_range <= min(prior_ranges)
                if not is_nr4:
                    rejected["not_nr4"] += 1
                    asian_range_history.append(asian_range)
                    continue
            elif not self.use_nr4:
                pass  # NR4 disabled — take all qualified days
            else:
                # Warming up NR4 history — skip (can't confirm compression rank)
                asian_range_history.append(asian_range)
                continue

            asian_range_history.append(asian_range)

            # ── TSMOM direction filter (Moskowitz et al. 2012) ───────────
            allowed_direction = "BOTH"
            if self.use_tsmom:
                allowed_direction = _tsmom_signal(daily_closes, eval_dt)

            # ── ATR(14) from H1 history ending at 05:00 bar ──────────────
            # Use last 60 H1 bars available up to and including the 05:00 bar
            # (captures ~2.5 days of history across the Asian trough)
            all_prior = df_h1[df_h1.index <= eval_dt.replace(hour=5, minute=0)]
            if len(all_prior) < _ATR_PERIOD + 2:
                rejected["insufficient_atr_data"] += 1
                continue
            atr_slice = all_prior.tail(_ATR_LOOKBACK)
            atr = wilder_atr_scalar(
                atr_slice["high"].values,
                atr_slice["low"].values,
                atr_slice["close"].values,
                period=_ATR_PERIOD,
            )
            if np.isnan(atr) or atr <= 0:
                rejected["invalid_atr"] += 1
                continue

            # ── Entry thresholds ──────────────────────────────────────────
            entry_long_thresh  = asian_high + _ENTRY_MULT * atr
            entry_short_thresh = asian_low  - _ENTRY_MULT * atr

            # SL: opposite side of range plus same buffer
            sl_for_long  = asian_low  - _SL_MULT * atr
            sl_for_short = asian_high + _SL_MULT * atr

            # Round-number SL adjustment (Osler 2003)
            sl_for_long  = _adjust_sl_round_number(sl_for_long,  "LONG",  pip)
            sl_for_short = _adjust_sl_round_number(sl_for_short, "SHORT", pip)

            # ── Entry scan: bars 07:00–09:00 UTC ─────────────────────────
            entry_bars = day_df[day_df.index.hour.isin(_ENTRY_HOURS)].sort_index()

            entry_found   = False
            direction     = None
            entry_price   = None
            entry_time    = None
            sl            = None
            tp            = None
            risk_fraction = _NR4_RISK if is_nr4 else _BASE_RISK

            for _, ebar in entry_bars.iterrows():
                bar_close = float(ebar["close"])
                ebar_time = ebar.name

                # Check LONG breakout
                if (allowed_direction in ("LONG", "BOTH")
                        and bar_close >= entry_long_thresh):
                    direction   = "LONG"
                    # Apply half-spread at entry (worst-case fill)
                    entry_price = bar_close + spread / 2
                    sl_candidate = sl_for_long
                    sl_dist      = abs(entry_price - sl_candidate)
                    if sl_dist < pip * _MIN_SL_PIPS:
                        rejected["sl_too_small"] += 1
                        break
                    tp_price  = entry_price + self.rr_target * sl_dist
                    sl        = sl_candidate
                    tp        = tp_price
                    entry_time = ebar_time
                    entry_found = True
                    break

                # Check SHORT breakout
                if (allowed_direction in ("SHORT", "BOTH")
                        and bar_close <= entry_short_thresh):
                    direction   = "SHORT"
                    entry_price = bar_close - spread / 2
                    sl_candidate = sl_for_short
                    sl_dist      = abs(entry_price - sl_candidate)
                    if sl_dist < pip * _MIN_SL_PIPS:
                        rejected["sl_too_small"] += 1
                        break
                    tp_price  = entry_price - self.rr_target * sl_dist
                    sl        = sl_candidate
                    tp        = tp_price
                    entry_time = ebar_time
                    entry_found = True
                    break

            if not entry_found:
                rejected["no_breakout"] += 1
                continue

            # ── Volatility-normalised size scaling (Barroso & Santa-Clara 2015) ──
            if self.vol_target is not None and len(daily_returns) >= 21:
                r21    = np.array(daily_returns[-21:])
                rv21   = float(np.std(r21, ddof=1) * math.sqrt(252))
                if rv21 > 0:
                    scale         = min(self.vol_target / rv21, 1.5)  # cap at 1.5×
                    risk_fraction = risk_fraction * scale

            # ── Track SL/TP/time-stop on remaining bars 07:00–11:00 UTC ──
            # Include entry bar onwards; SL/TP checked on OHLC of each bar
            tracking_bars = day_df[
                (day_df.index >= entry_time) &
                (day_df.index.hour <= _TIME_STOP_HOUR)
            ].sort_index()

            exit_price  = None
            exit_reason = None
            exit_time   = None

            for _, tbar in tracking_bars.iterrows():
                tbar_time  = tbar.name
                bar_hi     = float(tbar["high"])
                bar_lo     = float(tbar["low"])
                bar_close  = float(tbar["close"])

                # On the entry bar, we entered at entry_price (bar close + spread)
                # — can't be stopped on the same bar we entered
                if tbar_time == entry_time:
                    # Still check if this bar already passed our TP (gap/spike bar)
                    if direction == "LONG" and bar_hi >= tp:
                        exit_price  = tp
                        exit_reason = "TP"
                        exit_time   = tbar_time
                        break
                    if direction == "SHORT" and bar_lo <= tp:
                        exit_price  = tp
                        exit_reason = "TP"
                        exit_time   = tbar_time
                        break
                    continue

                # SL check (conservative: SL before TP if bar spans both)
                if direction == "LONG":
                    if bar_lo <= sl:
                        exit_price, exit_reason = sl, "SL"
                        exit_time = tbar_time
                        break
                    if bar_hi >= tp:
                        exit_price, exit_reason = tp, "TP"
                        exit_time = tbar_time
                        break
                else:  # SHORT
                    if bar_hi >= sl:
                        exit_price, exit_reason = sl, "SL"
                        exit_time = tbar_time
                        break
                    if bar_lo <= tp:
                        exit_price, exit_reason = tp, "TP"
                        exit_time = tbar_time
                        break

                # Time stop: if this is the last bar we track, exit at close
                if tbar_time.hour == _TIME_STOP_HOUR:
                    exit_price  = bar_close
                    exit_reason = "TIME"
                    exit_time   = tbar_time
                    break

            if exit_price is None:
                # Reached end of day data before time stop bar — exit at last close
                last_bar    = tracking_bars.iloc[-1]
                exit_price  = float(last_bar["close"])
                exit_reason = "TIME_EOD"
                exit_time   = last_bar.name

            # ── P&L calculation (same convention as lcr_backtest / mr_backtest) ──
            dist    = abs(exit_price - entry_price)
            sl_dist = abs(sl - entry_price)
            sign    = 1 if (direction == "LONG") == (exit_price > entry_price) else -1
            pl_pips = sign * dist / pip
            pl_pct  = sign * (dist / sl_dist) * risk_fraction   # fixed-fractional
            balance *= 1.0 + pl_pct

            # Daily return for vol targeting
            daily_returns.append(pl_pct)

            trades.append({
                "entry_time": entry_time.isoformat(),
                "exit_time":  exit_time.isoformat() if exit_time is not None else "",
                "direction":  direction,
                "entry":      round(entry_price, 5 if pip < 0.005 else 3),
                "exit":       round(exit_price,  5 if pip < 0.005 else 3),
                "sl":         round(sl,           5 if pip < 0.005 else 3),
                "tp":         round(tp,           5 if pip < 0.005 else 3),
                "reason":     exit_reason,
                "pl_pips":    round(pl_pips, 1),
                "pl_pct":     round(pl_pct * 100, 3),
                "balance":    round(balance, 2),
                "is_nr4":     is_nr4,
                "tsmom":      allowed_direction,
                "asian_range_pips": round(range_pips, 1),
                "atr_pips":   round(atr / pip, 1),
            })

        return self._build_result(instrument, trades, rejected, balance)

    # ── Statistics ────────────────────────────────────────────────────────────

    def _build_result(
        self,
        instrument: str,
        trades: list[dict],
        rejected: dict,
        final_balance: float,
    ) -> dict:
        if not trades:
            return {
                "instrument": instrument,
                "trades": [],
                "stats": {"n_trades": 0},
                "rejected": dict(rejected),
            }

        wins   = [t for t in trades if t["pl_pips"] > 0]
        losses = [t for t in trades if t["pl_pips"] <= 0]
        pl_pct = [t["pl_pct"] for t in trades]

        gross_profit = sum(t["pl_pips"] for t in wins)
        gross_loss   = abs(sum(t["pl_pips"] for t in losses))
        pf           = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        equity = np.array([self.initial_balance] + [t["balance"] for t in trades])
        peak   = np.maximum.accumulate(equity)
        dd_arr = (equity - peak) / peak
        max_dd = float(np.min(dd_arr))

        # Per-trade returns as fraction (for Sharpe)
        daily_pl = np.array(pl_pct) / 100.0
        sr = float(np.mean(daily_pl)) / float(np.std(daily_pl, ddof=1)) * math.sqrt(len(daily_pl)) if np.std(daily_pl, ddof=1) > 0 else 0.0

        t_stat = _t_statistic(pl_pct)

        net_pct = (final_balance - self.initial_balance) / self.initial_balance * 100

        # Monthly breakdown
        monthly: dict[str, list] = defaultdict(list)
        for t in trades:
            monthly[_monthly_key(t["entry_time"])].append(t["pl_pips"])

        monthly_lines = []
        for ym, pips in sorted(monthly.items()):
            w  = [p for p in pips if p > 0]
            lo = [p for p in pips if p <= 0]
            gp = sum(w)
            gl = abs(sum(lo))
            mpf = f"{gp/gl:.2f}" if gl > 0 else "∞"
            monthly_lines.append(
                f"{ym}: {len(pips):>2} trades  "
                f"WR={len(w)/len(pips)*100:>4.0f}%  "
                f"PF={mpf}  "
                f"NR4={'yes' if any(t['is_nr4'] for t in trades if t['entry_time'][:7] == ym) else 'no'}"
            )

        # NR4 sub-stats
        nr4_trades = [t for t in trades if t["is_nr4"]]
        non_nr4    = [t for t in trades if not t["is_nr4"]]
        nr4_wr     = (len([t for t in nr4_trades if t["pl_pips"] > 0]) / len(nr4_trades) * 100) if nr4_trades else 0
        non_nr4_wr = (len([t for t in non_nr4   if t["pl_pips"] > 0]) / len(non_nr4)   * 100) if non_nr4   else 0

        # LONG vs SHORT sub-stats
        long_tr  = [t for t in trades if t["direction"] == "LONG"]
        short_tr = [t for t in trades if t["direction"] == "SHORT"]
        long_wr  = (len([t for t in long_tr  if t["pl_pips"] > 0]) / len(long_tr)  * 100) if long_tr  else 0
        short_wr = (len([t for t in short_tr if t["pl_pips"] > 0]) / len(short_tr) * 100) if short_tr else 0

        stats = {
            "n_trades":           len(trades),
            "win_rate_pct":       round(len(wins) / len(trades) * 100, 1),
            "profit_factor":      round(pf, 3),
            "net_pct":            round(net_pct, 2),
            "max_drawdown_pct":   round(max_dd * 100, 2),
            "trade_sharpe":       round(sr, 3),
            "t_statistic":        round(t_stat, 3),
            "avg_win_pips":       round(float(np.mean([t["pl_pips"] for t in wins])),   1) if wins   else 0,
            "avg_loss_pips":      round(float(np.mean([t["pl_pips"] for t in losses])), 1) if losses else 0,
            "avg_win_pct":        round(float(np.mean([t["pl_pct"]  for t in wins])),   3) if wins   else 0,
            "avg_loss_pct":       round(float(np.mean([t["pl_pct"]  for t in losses])), 3) if losses else 0,
            "trades_per_month":   round(len(trades) / max(1, len(monthly)), 1),
            "final_balance":      round(final_balance, 2),
            "nr4_trades":         len(nr4_trades),
            "nr4_win_rate_pct":   round(nr4_wr, 1),
            "non_nr4_win_rate_pct": round(non_nr4_wr, 1),
            "long_win_rate_pct":  round(long_wr, 1),
            "short_win_rate_pct": round(short_wr, 1),
            "monthly_breakdown":  monthly_lines,
        }

        return {
            "instrument": instrument,
            "trades":     trades,
            "stats":      stats,
            "rejected":   dict(rejected),
        }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_results(
    instrument: str,
    results: dict,
    label: str = "",
    permtest: bool = False,
) -> None:
    stats   = results.get("stats", {})
    monthly = stats.pop("monthly_breakdown", [])
    trades  = results.get("trades", [])
    rej     = results.get("rejected", {})

    header = f"ACEB Backtest — {instrument}" + (f"  [{label}]" if label else "")
    print(f"\n{'='*60}")
    print(header)
    print(f"{'='*60}")

    core_keys = [
        "n_trades", "win_rate_pct", "profit_factor", "net_pct",
        "max_drawdown_pct", "trade_sharpe", "t_statistic",
        "avg_win_pips", "avg_loss_pips",
        "trades_per_month", "final_balance",
    ]
    for k in core_keys:
        if k in stats:
            print(f"  {k:<28} {stats[k]}")

    sig = "✓ SIGNIFICANT (t > 2.0)" if stats.get("t_statistic", 0) >= 2.0 else "✗ not significant"
    print(f"\n  Edge significance:           {sig}")

    print(f"\n  NR4 trades / WR:             {stats.get('nr4_trades',0)}  /  {stats.get('nr4_win_rate_pct',0):.1f}%")
    print(f"  Non-NR4 WR (comparison):     {stats.get('non_nr4_win_rate_pct',0):.1f}%")
    print(f"  Long WR / Short WR:          {stats.get('long_win_rate_pct',0):.1f}%  /  {stats.get('short_win_rate_pct',0):.1f}%")

    if rej:
        print("\n  Rejection breakdown:")
        total_rej = sum(rej.values())
        for reason, count in sorted(rej.items(), key=lambda x: -x[1]):
            print(f"    {reason:<30} {count:>4}  ({count/max(1,total_rej)*100:.0f}%)")

    if permtest and trades:
        pl_pct = [t["pl_pct"] for t in trades]
        mean_pct, p_val = _permutation_test(pl_pct)
        sig_p = "✓" if p_val < 0.05 else "✗"
        print("\n  Permutation test (10 000 shuffles):")
        print(f"    Mean trade return:         {mean_pct:+.4f}%")
        print(f"    Empirical p-value:         {p_val:.4f}  {sig_p}")

    if monthly:
        print("\n  Monthly breakdown:")
        for line in monthly:
            print(f"    {line}")

    print(f"{'='*60}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Asian Compression-Expansion Breakout (ACEB) backtester"
    )
    parser.add_argument("--instrument", required=True,
                        help="e.g. EUR_USD")
    parser.add_argument("--h1-csv",  required=True,
                        help="Path to H1 OHLCV CSV")
    parser.add_argument("--d-csv",   default=None,
                        help="Path to Daily OHLCV CSV (for TSMOM; derived from H1 if omitted)")
    parser.add_argument("--balance", type=float, default=10_000.0,
                        help="Starting account balance")
    parser.add_argument("--start",   default=None,
                        help="Backtest start date YYYY-MM-DD")
    parser.add_argument("--end",     default=None,
                        help="Backtest end date YYYY-MM-DD")
    parser.add_argument("--split",   default=None,
                        help="In-sample / OOS split date YYYY-MM-DD")
    parser.add_argument("--rr",      type=float, default=_RR_TARGET,
                        help=f"Risk:reward ratio (default {_RR_TARGET})")
    parser.add_argument("--no-nr4",  action="store_true",
                        help="Disable NR4 filter (ablation test)")
    parser.add_argument("--no-tsmom", action="store_true",
                        help="Disable TSMOM direction filter (ablation test)")
    parser.add_argument("--vol-target", type=float, default=None,
                        help="Volatility-normalised sizing target (e.g. 0.08). "
                             "Default: fixed fractional only.")
    parser.add_argument("--permtest", action="store_true",
                        help="Run 10 000-shuffle permutation test (slow ~5 s)")
    parser.add_argument("--export-csv", default=None,
                        help="Export trade log to this CSV path")
    args = parser.parse_args()

    df_h1    = _load_csv(args.h1_csv)
    df_daily = _load_csv(args.d_csv) if args.d_csv else None

    use_nr4   = not args.no_nr4
    use_tsmom = not args.no_tsmom

    engine = ACEBBacktestEngine(
        initial_balance=args.balance,
        use_nr4=use_nr4,
        use_tsmom=use_tsmom,
        rr_target=args.rr,
        vol_target=args.vol_target,
    )

    if args.split:
        # ── In-sample run ────────────────────────────────────────────────
        is_results = engine.run(
            instrument=args.instrument,
            df_h1=df_h1,
            df_daily=df_daily,
            start=args.start,
            end=args.split,
        )
        _print_results(
            args.instrument, is_results,
            label=f"IN-SAMPLE  ≤ {args.split}",
            permtest=args.permtest,
        )

        # ── Out-of-sample run ────────────────────────────────────────────
        oos_results = engine.run(
            instrument=args.instrument,
            df_h1=df_h1,
            df_daily=df_daily,
            start=args.split,
            end=args.end,
        )
        _print_results(
            args.instrument, oos_results,
            label=f"OOS  ≥ {args.split}",
            permtest=args.permtest,
        )

        # ── Degradation check ────────────────────────────────────────────
        is_wr  = is_results.get("stats", {}).get("win_rate_pct", 0)
        oos_wr = oos_results.get("stats", {}).get("win_rate_pct", 0)
        is_pf  = is_results.get("stats", {}).get("profit_factor", 0)
        oos_pf = oos_results.get("stats", {}).get("profit_factor", 0)
        print(f"\n  IS→OOS degradation: WR {is_wr:.1f}%→{oos_wr:.1f}%  "
              f"PF {is_pf:.2f}→{oos_pf:.2f}")
        if oos_pf > 1.0:
            print("  ✓ Strategy holds out-of-sample (PF > 1.0 OOS)")
        else:
            print("  ✗ OOS PF < 1.0 — edge did not survive holdout")

        if args.export_csv:
            all_trades = (
                is_results.get("trades", []) + oos_results.get("trades", [])
            )
            pd.DataFrame(all_trades).to_csv(args.export_csv, index=False)
            print(f"\n  Trade log exported → {args.export_csv}")

    else:
        # ── Full-period run ──────────────────────────────────────────────
        results = engine.run(
            instrument=args.instrument,
            df_h1=df_h1,
            df_daily=df_daily,
            start=args.start,
            end=args.end,
        )
        _print_results(
            args.instrument, results,
            permtest=args.permtest,
        )

        if args.export_csv and results.get("trades"):
            pd.DataFrame(results["trades"]).to_csv(args.export_csv, index=False)
            print(f"\n  Trade log exported → {args.export_csv}")


if __name__ == "__main__":
    main()
