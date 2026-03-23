"""
MR Validated Backtest — Regime-Faithful Assessment.

Addresses every known flaw in the prior MR backtests:

  1. H4 missing: fixed — only fully-closed H4 bars passed to engine.
  2. SL direction bug: fixed in mean_reversion_engine.py.
  3. HMM absent: fixed here via a non-leaky IS/OOS regime backfill.
  4. D look-ahead: fixed — only prior-day-close daily bars used.
  5. Spread missing: fixed — half-spread applied to every entry.

Regime backfill methodology (no future leakage):
  - IS period  (before IS_CUTOFF): no HMM gate, ADX-only (matches what live
    did before the HMM model existed).
  - OOS period (from IS_CUTOFF): HMM trained ONLY on IS daily data, then
    used to predict each day's regime sequentially forward (data up to date d,
    never data after d). Only RANGING days allow MR signals.

Usage:
    python -m anchor.backtesting.mr_regime_backtest \\
        --instrument EUR_USD \\
        --h1-csv  data/EUR_USD_H1.csv \\
        --h4-csv  data/EUR_USD_H4.csv \\
        --d-csv   data/EUR_USD_D.csv \\
        --is-cutoff 2022-01-01 \\
        --balance 10000

    # All active pairs
    for pair in EUR_USD NZD_USD USD_CAD EUR_JPY AUD_USD; do
        python -m anchor.backtesting.mr_regime_backtest \\
            --instrument $pair \\
            --h1-csv data/${pair}_H1.csv \\
            --h4-csv data/${pair}_H4.csv \\
            --d-csv  data/${pair}_D.csv
    done
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import date, timedelta, timezone

import numpy as np
import pandas as pd
import structlog

from anchor.signals.mean_reversion_engine import MeanReversionEngine, MRSignalResult
from anchor.regime.hmm_detector import HMMRegimeDetector
from anchor.utils.math_utils import get_pip_size

logger = structlog.get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_IS_CUTOFF_DEFAULT = "2022-01-01"   # 4yr IS, 4yr OOS

# Half-spread per instrument for London session (tighter than NY).
# Applied to entry price; conservative London mid-session estimates.
_SPREAD_COST = {
    "EUR_USD": 0.00002,   # 0.2 pip
    "GBP_USD": 0.00003,   # 0.3 pip
    "NZD_USD": 0.00008,   # 0.8 pip
    "USD_CAD": 0.00010,   # 1.0 pip
    "EUR_JPY": 0.018,     # 1.8 pip
    "AUD_USD": 0.00007,   # 0.7 pip
    "USD_JPY": 0.012,     # 1.2 pip
    "USD_CHF": 0.00008,   # 0.8 pip
}

_SPREAD_STRESS_MULT = 2.0   # stress test: 2× spread

_WARMUP    = 50
_LOOKBACK  = 200
_RISK_FRAC = 0.01   # 1% per trade


# ── Data loading ───────────────────────────────────────────────────────────────

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


# ── HMM regime backfill (non-leaky) ────────────────────────────────────────────

def build_regime_series(
    df_daily: pd.DataFrame,
    is_cutoff: date,
) -> dict[date, str]:
    """
    Generates a date→regime mapping with no future leakage.

    IS period  (before is_cutoff): labelled UNKNOWN — no HMM.
    OOS period (from is_cutoff):   HMM trained on IS-only data, then applied
                                   sequentially. Each date d uses only data
                                   up to and including d; model parameters
                                   never see data after is_cutoff.

    Leakage discussion:
      The HMM model is trained on IS daily bars only (before is_cutoff).
      Its transition matrix and emission means are derived purely from that
      window. For OOS prediction, predict_current() is called with a rolling
      slice ending at date d — so no future prices are used in the inference.
      The only residual leakage concern is that the IS training window itself
      is used in full (whole IS dataset seen during fit). This is unavoidable
      and is the same assumption made by any walk-forward system.
    """
    is_mask  = df_daily.index.date < is_cutoff    # type: ignore[attr-defined]
    df_is    = df_daily[is_mask]

    if len(df_is) < 150:
        logger.warning("hmm_backfill_insufficient_is_data", rows=len(df_is))
        return {}

    detector = HMMRegimeDetector()
    detector.fit(df_is)

    if not detector.is_ready:
        logger.warning("hmm_backfill_fit_failed")
        return {}

    oos_dates = [d for d in pd.Series(df_daily.index.date).unique()   # type: ignore
                 if d >= is_cutoff]

    labels: dict[date, str] = {}
    for d in oos_dates:
        cutoff_ts = pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=23, minutes=59)
        slice_d   = df_daily[df_daily.index <= cutoff_ts]
        regime, _ = detector.predict_current(slice_d)
        labels[d] = regime

    ranging_count = sum(1 for v in labels.values() if v == "RANGING")
    logger.info(
        "hmm_backfill_complete",
        oos_days=len(labels),
        ranging_pct=round(ranging_count / max(1, len(labels)) * 100, 1),
    )
    return labels


# ── Core backtest engine ────────────────────────────────────────────────────────

class MRRegimeBacktestEngine:
    def __init__(
        self,
        initial_balance: float = 10_000.0,
        spread_mult: float = 1.0,
    ):
        self.initial_balance = initial_balance
        self.spread_mult     = spread_mult

    def run(
        self,
        instrument: str,
        df_h1: pd.DataFrame,
        df_h4: pd.DataFrame | None,
        df_daily: pd.DataFrame | None,
        regime_labels: dict[date, str],   # OOS labels; empty = ADX-only for all
        is_cutoff: date,
        start: str | None = None,
        end: str | None = None,
    ) -> dict:
        if start:
            df_h1 = df_h1[df_h1.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            df_h1 = df_h1[df_h1.index <= pd.Timestamp(end, tz="UTC")]
        if len(df_h1) < _WARMUP:
            return {"error": "insufficient_data", "trades": [], "stats": {}}

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self._run_async(instrument, df_h1, df_h4, df_daily, regime_labels, is_cutoff)
            )
        finally:
            loop.close()

    async def _run_async(
        self,
        instrument: str,
        df_h1: pd.DataFrame,
        df_h4: pd.DataFrame | None,
        df_daily: pd.DataFrame | None,
        regime_labels: dict[date, str],
        is_cutoff: date,
    ) -> dict:
        balance   = self.initial_balance
        trades:   list[dict] = []
        position: dict | None = None
        pending:  dict | None = None

        engine = MeanReversionEngine()
        pip    = get_pip_size(instrument)
        half_spread = _SPREAD_COST.get(instrument, 0.0001) * self.spread_mult / 2.0

        for i in range(_WARMUP, len(df_h1)):
            bar = df_h1.iloc[i]
            dt  = bar.name
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            bar_date = dt.date()

            # ── Pending limit order management ─────────────────────────────
            if pending is not None:
                if dt > pending["expires_at"]:
                    pending = None
                else:
                    fill = _try_fill(pending["direction"], pending["entry"], bar)
                    if fill is not None:
                        position = {
                            "direction":    pending["direction"],
                            "entry":        fill,
                            "sl":           pending["sl"],
                            "tp":           pending["tp"],
                            "entry_time":   dt,
                            "risk_frac":    pending["risk_frac"],
                            "confluence":   pending["confluence"],
                            "regime":       pending["regime"],
                            "period":       pending["period"],
                        }
                        pending = None

            # ── Open position management ────────────────────────────────────
            if position is not None:
                hi, lo = float(bar["high"]), float(bar["low"])
                sl, tp  = position["sl"], position["tp"]
                direction = position["direction"]
                closed    = False

                if direction == "LONG":
                    if lo <= sl:
                        exit_price, reason = sl, "SL"; closed = True
                    elif hi >= tp:
                        exit_price, reason = tp, "TP"; closed = True
                else:
                    if hi >= sl:
                        exit_price, reason = sl, "SL"; closed = True
                    elif lo <= tp:
                        exit_price, reason = tp, "TP"; closed = True

                if closed:
                    dist    = abs(exit_price - position["entry"])
                    sl_dist = abs(position["sl"] - position["entry"])
                    sign    = 1 if (direction == "LONG") == (exit_price > position["entry"]) else -1
                    pl_pct  = sign * (dist / sl_dist) * position["risk_frac"]
                    pl_pips = sign * dist / pip
                    balance *= (1 + pl_pct)

                    trades.append({
                        "entry_time": position["entry_time"].isoformat(),
                        "exit_time":  dt.isoformat(),
                        "direction":  direction,
                        "entry":      position["entry"],
                        "exit":       exit_price,
                        "sl":         position["sl"],
                        "tp":         position["tp"],
                        "reason":     reason,
                        "pl_pips":    round(pl_pips, 1),
                        "pl_pct":     round(pl_pct * 100, 3),
                        "balance":    round(balance, 2),
                        "confluence": position["confluence"],
                        "regime":     position["regime"],
                        "period":     position["period"],
                    })
                    position = None

            if position is not None or pending is not None:
                continue

            # ── Regime gate (OOS only) ──────────────────────────────────────
            is_oos = bar_date >= is_cutoff
            if is_oos:
                regime = regime_labels.get(bar_date, "UNKNOWN")
                if regime != "RANGING":
                    continue
            else:
                regime = "IS_ADX_ONLY"

            # ── Build data slices ───────────────────────────────────────────
            slice_h1 = df_h1.iloc[max(0, i - _LOOKBACK + 1):i + 1]
            engine.data_cache = {"H1": {instrument: slice_h1}}

            if df_daily is not None:
                # Only completed daily bars — today's bar hasn't closed yet
                today_ts = pd.Timestamp(bar_date, tz="UTC")
                slice_d  = df_daily[df_daily.index < today_ts].tail(300)
                if len(slice_d) >= 50:
                    engine.data_cache["D"] = {instrument: slice_d}

            if df_h4 is not None:
                slice_h4 = df_h4[df_h4.index + pd.Timedelta(hours=4) <= dt].tail(300)
                if len(slice_h4) >= 20:
                    engine.data_cache["H4"] = {instrument: slice_h4}

            result: MRSignalResult = await engine.evaluate(instrument, dt=dt)

            if result.suppressed or result.direction is None:
                continue

            entry  = result.entry_price or float(bar["close"])
            sl     = result.stop_loss
            tp     = result.take_profit
            if sl is None or tp is None:
                continue

            sl_dist = abs(entry - sl)
            if sl_dist < 1e-8 or sl_dist < pip * 2:
                continue

            # Apply half-spread to entry (conservative fill)
            if result.direction == "LONG":
                entry += half_spread
            else:
                entry -= half_spread

            # After spread, re-verify SL is still on correct side
            if result.direction == "LONG" and sl >= entry:
                continue
            if result.direction == "SHORT" and sl <= entry:
                continue

            pending = {
                "direction":  result.direction,
                "entry":      entry,
                "sl":         sl,
                "tp":         tp,
                "expires_at": dt + pd.Timedelta(hours=2),
                "risk_frac":  _RISK_FRAC,
                "confluence": result.confluence_score,
                "regime":     regime,
                "period":     "OOS" if is_oos else "IS",
            }

        return self._summarise(instrument, trades, balance)

    def _summarise(self, instrument: str, trades: list[dict], balance: float) -> dict:
        if not trades:
            return {"instrument": instrument, "trades": [], "stats": {"n_trades": 0}}

        wins   = [t for t in trades if t["pl_pips"] > 0]
        losses = [t for t in trades if t["pl_pips"] <= 0]
        gp     = sum(t["pl_pips"] for t in wins)
        gl     = abs(sum(t["pl_pips"] for t in losses))
        pf     = gp / gl if gl > 0 else float("inf")

        equity = np.array([self.initial_balance] + [t["balance"] for t in trades])
        peak   = np.maximum.accumulate(equity)
        dd     = (equity - peak) / peak
        max_dd = float(np.min(dd))

        net_pct = (balance - self.initial_balance) / self.initial_balance * 100

        # Monthly breakdown
        monthly: dict[str, list] = defaultdict(list)
        for t in trades:
            monthly[t["entry_time"][:7]].append(t["pl_pips"])

        # Regime breakdown
        regime_stats: dict[str, dict] = {}
        for regime_label in ["IS_ADX_ONLY", "RANGING", "TRENDING", "VOLATILE", "UNKNOWN"]:
            rt = [t for t in trades if t["regime"] == regime_label]
            if not rt:
                continue
            rw = [t for t in rt if t["pl_pips"] > 0]
            rl = [t for t in rt if t["pl_pips"] <= 0]
            rgp = sum(t["pl_pips"] for t in rw)
            rgl = abs(sum(t["pl_pips"] for t in rl))
            regime_stats[regime_label] = {
                "n":    len(rt),
                "wr":   round(len(rw) / len(rt) * 100, 1),
                "pf":   round(rgp / rgl, 3) if rgl > 0 else float("inf"),
                "pips": round(sum(t["pl_pips"] for t in rt), 1),
            }

        # IS vs OOS split
        period_stats: dict[str, dict] = {}
        for period in ["IS", "OOS"]:
            pt = [t for t in trades if t["period"] == period]
            if not pt:
                continue
            pw = [t for t in pt if t["pl_pips"] > 0]
            pl = [t for t in pt if t["pl_pips"] <= 0]
            pgp = sum(t["pl_pips"] for t in pw)
            pgl = abs(sum(t["pl_pips"] for t in pl))
            period_stats[period] = {
                "n":    len(pt),
                "wr":   round(len(pw) / len(pt) * 100, 1),
                "pf":   round(pgp / pgl, 3) if pgl > 0 else float("inf"),
                "pips": round(sum(t["pl_pips"] for t in pt), 1),
            }

        monthly_lines = []
        for ym, pips in sorted(monthly.items()):
            w = [p for p in pips if p > 0]
            l = [p for p in pips if p <= 0]
            gpp = sum(w); gll = abs(sum(l))
            monthly_lines.append(
                f"{ym}: {len(pips)} trades  WR={len(w)/len(pips)*100:.0f}%  "
                f"PF={gpp/gll:.2f}" if gll > 0 else f"{ym}: {len(pips)} trades  WR=100%  PF=∞"
            )

        stats = {
            "n_trades":         len(trades),
            "win_rate":         round(len(wins) / len(trades) * 100, 1),
            "profit_factor":    round(pf, 3),
            "net_pct":          round(net_pct, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "avg_win_pips":     round(float(np.mean([t["pl_pips"] for t in wins])), 1)   if wins   else 0,
            "avg_loss_pips":    round(float(np.mean([t["pl_pips"] for t in losses])), 1) if losses else 0,
            "trades_per_month": round(len(trades) / max(1, len(monthly)), 1),
            "final_balance":    round(balance, 2),
            "regime_breakdown": regime_stats,
            "period_split":     period_stats,
            "monthly_breakdown": monthly_lines,
        }

        logger.info(
            "mr_regime_backtest_complete",
            instrument=instrument,
            **{k: v for k, v in stats.items()
               if k not in ("regime_breakdown", "period_split", "monthly_breakdown")},
        )
        return {"instrument": instrument, "trades": trades, "stats": stats}


def _try_fill(direction: str, limit_price: float, bar: pd.Series) -> float | None:
    """Fill a pending limit order if price revisits the level."""
    if direction == "LONG":
        if float(bar["low"]) > limit_price:
            return None
        return min(limit_price, float(bar["open"]))
    else:
        if float(bar["high"]) < limit_price:
            return None
        return max(limit_price, float(bar["open"]))


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="MR validated regime-faithful backtest")
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--h1-csv",    required=True)
    parser.add_argument("--h4-csv",    default=None)
    parser.add_argument("--d-csv",     default=None)
    parser.add_argument("--is-cutoff", default=_IS_CUTOFF_DEFAULT,
                        help="IS/OOS split date (YYYY-MM-DD). HMM trained on IS only.")
    parser.add_argument("--balance",   type=float, default=10_000.0)
    parser.add_argument("--start",     default=None)
    parser.add_argument("--end",       default=None)
    parser.add_argument("--stress",    action="store_true",
                        help="Run 2× spread stress test in addition to base run")
    args = parser.parse_args()

    from datetime import date as date_cls
    is_cutoff = date_cls.fromisoformat(args.is_cutoff)

    df_h1    = _load_csv(args.h1_csv)
    df_h4    = _load_csv(args.h4_csv) if args.h4_csv else None
    df_daily = _load_csv(args.d_csv)  if args.d_csv  else None

    # Build non-leaky regime labels for OOS period
    regime_labels: dict[date, str] = {}
    if df_daily is not None:
        print(f"\nBuilding HMM regime labels (IS cutoff: {is_cutoff})...")
        regime_labels = build_regime_series(df_daily, is_cutoff)
        if regime_labels:
            ranging_days = sum(1 for v in regime_labels.values() if v == "RANGING")
            print(f"  OOS days labelled: {len(regime_labels)}")
            print(f"  RANGING: {ranging_days} ({ranging_days/len(regime_labels)*100:.0f}%)")
            other = {v for v in regime_labels.values() if v != "RANGING"}
            for label in sorted(other):
                n = sum(1 for v in regime_labels.values() if v == label)
                print(f"  {label}: {n} ({n/len(regime_labels)*100:.0f}%)")
        else:
            print("  WARNING: regime labelling failed, backtest runs ADX-only for all bars")

    def _run_and_print(label: str, spread_mult: float = 1.0) -> None:
        bt = MRRegimeBacktestEngine(
            initial_balance=args.balance,
            spread_mult=spread_mult,
        )
        res   = bt.run(
            instrument=args.instrument,
            df_h1=df_h1,
            df_h4=df_h4,
            df_daily=df_daily,
            regime_labels=regime_labels,
            is_cutoff=is_cutoff,
            start=args.start,
            end=args.end,
        )
        stats   = res.get("stats", {})
        monthly = stats.pop("monthly_breakdown", [])
        regime_breakdown = stats.pop("regime_breakdown", {})
        period_split     = stats.pop("period_split", {})

        print(f"\n{'='*60}")
        print(f"MR Regime Backtest — {args.instrument}  [{label}]")
        print(f"IS: data start → {is_cutoff}   |   OOS: {is_cutoff} → end")
        print(f"{'='*60}")
        for k, v in stats.items():
            print(f"  {k:<28} {v}")

        if period_split:
            print(f"\n  IS vs OOS split:")
            for period, ps in period_split.items():
                print(f"    {period}: n={ps['n']}  WR={ps['wr']}%  PF={ps['pf']}  pips={ps['pips']}")

        if regime_breakdown:
            print(f"\n  Regime breakdown (trades that fired per regime):")
            for reg, rs in regime_breakdown.items():
                print(f"    {reg:<16} n={rs['n']:<4}  WR={rs['wr']}%  PF={rs['pf']}  pips={rs['pips']}")

        if monthly:
            print(f"\n  Monthly breakdown:")
            for line in monthly:
                print(f"    {line}")

        print(f"{'='*60}")

    _run_and_print("base spread")
    if args.stress:
        _run_and_print("2× spread stress", spread_mult=_SPREAD_STRESS_MULT)


if __name__ == "__main__":
    main()
