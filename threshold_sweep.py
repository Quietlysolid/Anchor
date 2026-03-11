"""
Threshold sweep: test confluence thresholds 0.55, 0.60, 0.65, 0.70 
to find optimal trade frequency vs accuracy balance.
Runs on OOS period (2024+) with lookback from 2023-07-01.
"""
import sys
sys.path.insert(0, '/app')

import asyncio
import numpy as np
import pandas as pd
from pathlib import Path

INSTRUMENT = "EUR_USD"
ATR_SL = 1.5
ATR_TP = 2.0
THRESHOLDS = [0.55, 0.60, 0.65, 0.70, 0.75]
OOS_START = "2024-01-01"

def load(path, start=None):
    df = pd.read_csv(path, parse_dates=["time"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time").reset_index(drop=True)
    if start:
        # keep 200 bars of lookback before OOS start
        oos_idx = df[df["time"] >= start].index
        if len(oos_idx) == 0:
            return df
        lb = max(0, oos_idx[0] - 200)
        df = df.iloc[lb:].reset_index(drop=True)
    return df

h1 = load("/tmp/data/EUR_USD_H1.csv", OOS_START)
h4 = load("/tmp/data/EUR_USD_H4.csv", OOS_START)
d1 = load("/tmp/data/EUR_USD_D.csv", OOS_START)
oos_start_dt = pd.Timestamp(OOS_START, tz="UTC")

print(f"H1 bars loaded: {len(h1)} (OOS from {OOS_START})")

from anchor.signals.engine import ConfluenceEngine
from anchor.risk.weekend_guard import WeekendGuard
from anchor.risk.holiday_calendar import is_holiday

wg = WeekendGuard()

def run_threshold(threshold: float) -> dict:
    data_cache = {"H1": {}, "H4": {}, "D": {}}
    engine = ConfluenceEngine(data_cache=data_cache, ml_classifier=None, feature_engineer=None)
    
    loop = asyncio.new_event_loop()
    trades = []
    open_trade = None
    account = 10000.0
    
    # ATR calc
    def compute_atr(window, period=14):
        closes = window["close"].values
        highs = window["high"].values
        lows = window["low"].values
        prev_c = np.roll(closes, 1); prev_c[0] = closes[0]
        tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_c), np.abs(lows - prev_c)))
        atr = float(np.mean(tr[:period]))
        a = 1.0 / period
        for v in tr[period:]:
            atr = a * float(v) + (1.0 - a) * atr
        return atr
    
    try:
        for i in range(200, len(h1)):
            bar = h1.iloc[i]
            bar_time = bar["time"]
            
            # Skip weekends/holidays
            if wg._is_close_time(bar_time) or is_holiday(bar_time):
                continue
            
            # Update open trade
            if open_trade:
                d = open_trade["direction"]
                sl = open_trade["sl"]
                tp = open_trade["tp"]
                entry = open_trade["entry"]
                
                hit_tp = bar["high"] >= tp if d == "LONG" else bar["low"] <= tp
                hit_sl = bar["low"] <= sl if d == "LONG" else bar["high"] >= sl
                
                if hit_sl and hit_tp:
                    # Both: assume SL first (conservative)
                    pnl = sl - entry if d == "LONG" else entry - sl
                    open_trade["exit"] = sl
                    open_trade["pnl"] = pnl
                    trades.append(open_trade)
                    open_trade = None
                elif hit_tp:
                    pnl = tp - entry if d == "LONG" else entry - tp
                    open_trade["exit"] = tp
                    open_trade["pnl"] = pnl
                    trades.append(open_trade)
                    open_trade = None
                elif hit_sl:
                    pnl = sl - entry if d == "LONG" else entry - sl
                    open_trade["exit"] = sl
                    open_trade["pnl"] = pnl
                    trades.append(open_trade)
                    open_trade = None
                else:
                    continue  # still open, skip signal
            
            # Only evaluate after OOS start
            if bar_time < oos_start_dt:
                continue
            
            h1_w = h1.iloc[max(0, i-200):i]
            h4_w = None
            if h4 is not None and len(h4) > 0:
                mask = h4["time"] <= bar_time
                if mask.sum() >= 55:
                    h4_w = h4[mask].iloc[-100:]
            d1_w = None
            if d1 is not None and len(d1) > 0:
                mask = d1["time"] <= bar_time
                if mask.sum() >= 50:
                    d1_w = d1[mask].iloc[-50:]
            
            engine.update_cache(INSTRUMENT, "H1", h1_w)
            if h4_w is not None:
                engine.update_cache(INSTRUMENT, "H4", h4_w)
            if d1_w is not None:
                engine.update_cache(INSTRUMENT, "D", d1_w)
            
            try:
                sig = loop.run_until_complete(engine.evaluate(instrument=INSTRUMENT, dt=bar_time))
            except Exception:
                continue
            
            if sig.suppressed or sig.direction is None:
                continue
            if sig.confluence_score < threshold:
                continue
            
            atr = compute_atr(h1_w)
            entry = bar["close"]
            if sig.direction == "LONG":
                sl = entry - ATR_SL * atr
                tp = entry + ATR_TP * atr
            else:
                sl = entry + ATR_SL * atr
                tp = entry - ATR_TP * atr
            
            open_trade = {
                "direction": sig.direction,
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "score": sig.confluence_score,
                "time": bar_time,
            }
    finally:
        loop.close()
    
    if not trades:
        return {"threshold": threshold, "trades": 0, "wr": 0, "pf": 0, "net_pct": 0}
    
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr = len(wins) / len(pnls)
    pf = sum(wins) / abs(sum(losses)) if losses else float("inf")
    # Approximate net P&L in pips (normalize by ATR)
    
    return {
        "threshold": threshold,
        "trades": len(trades),
        "wr": wr * 100,
        "pf": pf,
        "avg_pnl": np.mean(pnls),
        "wins": len(wins),
        "losses": len(losses),
    }

print(f"\n{'Threshold':>10} {'Trades':>7} {'WR%':>7} {'PF':>7} {'AvgPnL':>9}")
print("-" * 48)
for t in THRESHOLDS:
    r = run_threshold(t)
    print(f"{r['threshold']:>10.2f} {r['trades']:>7} {r['wr']:>6.1f}% {r['pf']:>7.2f} {r.get('avg_pnl', 0):>9.5f}")

print("\nDone.")
