"""
Diagnose OOS trade quality by session, direction, score band, and month.
Uses the same signal generation but logs all trades.
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
THRESHOLD = 0.65
OOS_START = "2024-01-01"

def load(path, start=None, lookback=200):
    df = pd.read_csv(path, parse_dates=["time"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time").reset_index(drop=True)
    if start:
        oos_idx = df[df["time"] >= start].index
        if len(oos_idx) > 0:
            lb = max(0, oos_idx[0] - lookback)
            df = df.iloc[lb:].reset_index(drop=True)
    return df

h1 = load("/tmp/data/EUR_USD_H1.csv", OOS_START)
h4 = load("/tmp/data/EUR_USD_H4.csv", OOS_START, lookback=100)
d1 = load("/tmp/data/EUR_USD_D.csv", OOS_START, lookback=50)
oos_start_dt = pd.Timestamp(OOS_START, tz="UTC")

from anchor.signals.engine import ConfluenceEngine
from anchor.risk.weekend_guard import WeekendGuard
from anchor.risk.holiday_calendar import is_holiday

wg = WeekendGuard()
data_cache = {"H1": {}, "H4": {}, "D": {}}
engine = ConfluenceEngine(data_cache=data_cache, ml_classifier=None, feature_engineer=None)
loop = asyncio.new_event_loop()
trades = []
open_trade = None

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
        
        if wg._is_close_time(bar_time) or is_holiday(bar_time):
            continue
        
        if open_trade:
            d = open_trade["direction"]
            sl = open_trade["sl"]
            tp = open_trade["tp"]
            entry = open_trade["entry"]
            
            hit_tp = bar["high"] >= tp if d == "LONG" else bar["low"] <= tp
            hit_sl = bar["low"] <= sl if d == "LONG" else bar["high"] >= sl
            
            if hit_sl and hit_tp:
                pnl = sl - entry if d == "LONG" else entry - sl
                open_trade["exit"] = sl; open_trade["pnl"] = pnl; open_trade["result"] = "LOSS"
            elif hit_tp:
                pnl = tp - entry if d == "LONG" else entry - tp
                open_trade["exit"] = tp; open_trade["pnl"] = pnl; open_trade["result"] = "WIN"
            elif hit_sl:
                pnl = sl - entry if d == "LONG" else entry - sl
                open_trade["exit"] = sl; open_trade["pnl"] = pnl; open_trade["result"] = "LOSS"
            
            if "result" in open_trade:
                trades.append(open_trade)
                open_trade = None
            else:
                continue
        
        if bar_time < oos_start_dt:
            continue
        
        h1_w = h1.iloc[max(0, i-200):i]
        mask = h4["time"] <= bar_time
        h4_w = h4[mask].iloc[-100:] if mask.sum() >= 55 else None
        mask = d1["time"] <= bar_time
        d1_w = d1[mask].iloc[-50:] if mask.sum() >= 50 else None
        
        engine.update_cache(INSTRUMENT, "H1", h1_w)
        if h4_w is not None: engine.update_cache(INSTRUMENT, "H4", h4_w)
        if d1_w is not None: engine.update_cache(INSTRUMENT, "D", d1_w)
        
        try:
            sig = loop.run_until_complete(engine.evaluate(instrument=INSTRUMENT, dt=bar_time))
        except Exception:
            continue
        
        if sig.suppressed or sig.direction is None or sig.confluence_score < THRESHOLD:
            continue
        
        atr = compute_atr(h1_w)
        entry = bar["close"]
        if sig.direction == "LONG":
            sl = entry - ATR_SL * atr; tp = entry + ATR_TP * atr
        else:
            sl = entry + ATR_SL * atr; tp = entry - ATR_TP * atr
        
        open_trade = {
            "direction": sig.direction,
            "entry": entry, "sl": sl, "tp": tp,
            "score": sig.confluence_score,
            "time": bar_time,
            "session": sig.session,
            "rsi_score": sig.rsi_score,
            "bb_kc_score": sig.bb_kc_score,
            "adx_score": sig.adx_score,
            "mtf_score": sig.mtf_score,
            "atr": atr,
        }
finally:
    loop.close()

if not trades:
    print("No trades.")
else:
    df = pd.DataFrame(trades)
    df["win"] = df["result"] == "WIN"
    df["month"] = df["time"].dt.to_period("M")
    
    print(f"\nTotal trades: {len(df)}, WR: {df['win'].mean()*100:.1f}%")
    
    print("\n--- By Direction ---")
    for d, g in df.groupby("direction"):
        print(f"  {d}: {len(g)} trades, WR {g['win'].mean()*100:.1f}%")
    
    print("\n--- By Session ---")
    for s, g in df.groupby("session"):
        print(f"  {s}: {len(g)} trades, WR {g['win'].mean()*100:.1f}%")
    
    print("\n--- By Score Band ---")
    df["band"] = pd.cut(df["score"], [0.64, 0.70, 0.75, 0.80, 1.0], labels=["0.65-0.70","0.70-0.75","0.75-0.80","0.80+"])
    for b, g in df.groupby("band", observed=True):
        print(f"  {b}: {len(g)} trades, WR {g['win'].mean()*100:.1f}%")
    
    print("\n--- By Month ---")
    monthly = df.groupby("month").agg(trades=("win","count"), wr=("win","mean")).reset_index()
    for _, row in monthly.iterrows():
        print(f"  {row['month']}: {int(row['trades'])} trades, WR {row['wr']*100:.0f}%")
    
    print("\n--- RSI Score vs Outcome ---")
    df["rsi_band"] = pd.cut(df["rsi_score"], [0,0.1,0.2,0.3,1.0], labels=["0-0.1","0.1-0.2","0.2-0.3","0.3+"])
    for b, g in df.groupby("rsi_band", observed=True):
        print(f"  RSI {b}: {len(g)} trades, WR {g['win'].mean()*100:.1f}%")
    
    print("\n--- ADX Score vs Outcome ---")
    df["adx_band"] = pd.cut(df["adx_score"], [-0.1,0.1,0.2,0.3,1.0], labels=["low","0.1-0.2","0.2-0.3","high"])
    for b, g in df.groupby("adx_band", observed=True):
        print(f"  ADX {b}: {len(g)} trades, WR {g['win'].mean()*100:.1f}%")
