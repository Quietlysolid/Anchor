"""
TP multiplier sweep: test different TP/SL combinations.
Uses OOS period (2024+), LONDON session only vs all sessions.
"""
import sys
sys.path.insert(0, '/app')

import asyncio
import numpy as np
import pandas as pd

INSTRUMENT = "EUR_USD"
ATR_SL = 1.5
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

def run_test(atr_tp: float, session_filter: str = "ALL") -> dict:
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
                    open_trade["result"] = "LOSS"
                elif hit_tp:
                    open_trade["result"] = "WIN"
                elif hit_sl:
                    open_trade["result"] = "LOSS"
                
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
            
            if sig.suppressed or sig.direction is None or sig.confluence_score < 0.65:
                continue
            
            # Session filter
            if session_filter == "LONDON" and sig.session != "LONDON":
                continue
            if session_filter == "OVERLAP" and sig.session != "OVERLAP":
                continue
            
            atr = compute_atr(h1_w)
            entry = bar["close"]
            if sig.direction == "LONG":
                sl = entry - ATR_SL * atr; tp = entry + atr_tp * atr
            else:
                sl = entry + ATR_SL * atr; tp = entry - atr_tp * atr
            
            open_trade = {
                "direction": sig.direction,
                "entry": entry, "sl": sl, "tp": tp,
                "score": sig.confluence_score,
                "time": bar_time,
                "session": sig.session,
            }
    finally:
        loop.close()
    
    if not trades:
        return {"tp": atr_tp, "session": session_filter, "trades": 0, "wr": 0, "pf": 0}
    
    wins = sum(1 for t in trades if t["result"] == "WIN")
    losses = len(trades) - wins
    wr = wins / len(trades)
    pf = (wins * atr_tp) / (losses * ATR_SL) if losses > 0 else 999
    return {
        "tp": atr_tp, "session": session_filter,
        "trades": len(trades), "wins": wins, "losses": losses,
        "wr": wr * 100, "pf": pf,
        "breakeven_wr": ATR_SL / (ATR_SL + atr_tp) * 100,
    }

configs = [
    (1.5, "ALL"), (1.5, "LONDON"), (1.5, "OVERLAP"),
    (2.0, "ALL"), (2.0, "LONDON"), (2.0, "OVERLAP"),
    (2.5, "ALL"), (2.5, "LONDON"),
    (3.0, "ALL"), (3.0, "LONDON"),
]

print(f"\n{'TP':>5} {'Session':>8} {'Trades':>7} {'WR%':>7} {'PF':>7} {'BE_WR%':>8}")
print("-" * 50)
for atr_tp, session in configs:
    r = run_test(atr_tp, session)
    be = r.get("breakeven_wr", 0)
    above = "✓" if r["wr"] > be else "✗"
    print(f"{atr_tp:>5.1f} {session:>8} {r['trades']:>7} {r['wr']:>6.1f}% {r['pf']:>7.2f} {be:>7.1f}% {above}")

print("\nDone.")
