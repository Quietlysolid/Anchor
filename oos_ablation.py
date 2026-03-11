"""Fast OOS ablation — runs each variant on 2024-01-01 to present only."""
import sys
sys.path.insert(0, '/app')

import pandas as pd
import asyncio
import numpy as np
from pathlib import Path

# Load and slice to OOS period
def load_slice(path, start):
    df = pd.read_csv(path, parse_dates=["time"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time").reset_index(drop=True)
    return df[df["time"] >= start].reset_index(drop=True)

OOS_START = "2024-01-01"
INSTRUMENT = "EUR_USD"

h1 = load_slice("/tmp/data/EUR_USD_H1.csv", OOS_START)
h4 = load_slice("/tmp/data/EUR_USD_H4.csv", OOS_START)
d1 = load_slice("/tmp/data/EUR_USD_D.csv", OOS_START)

# Also need some lookback context — load pre-OOS for warm-up
h1_full = pd.read_csv("/tmp/data/EUR_USD_H1.csv", parse_dates=["time"])
h1_full["time"] = pd.to_datetime(h1_full["time"], utc=True)
h1_full = h1_full.sort_values("time").reset_index(drop=True)

h4_full = pd.read_csv("/tmp/data/EUR_USD_H4.csv", parse_dates=["time"])
h4_full["time"] = pd.to_datetime(h4_full["time"], utc=True)
h4_full = h4_full.sort_values("time").reset_index(drop=True)

d1_full = pd.read_csv("/tmp/data/EUR_USD_D.csv", parse_dates=["time"])
d1_full["time"] = pd.to_datetime(d1_full["time"], utc=True)
d1_full = d1_full.sort_values("time").reset_index(drop=True)

from anchor.backtesting.ablation_runner import ABLATION_TESTS, _AblatedBacktestEngine, AblationResult, print_ablation_table, export_csv

results = []
for test in ABLATION_TESTS:
    print(f"\n[{test.name}] {test.description}")
    eng = _AblatedBacktestEngine(initial_balance=10000, ablation_flags=test.flags)
    eng.load_df(INSTRUMENT, "H1", h1_full)  # full history for window lookback
    eng.load_df(INSTRUMENT, "H4", h4_full)
    eng.load_df(INSTRUMENT, "D", d1_full)
    
    # Restrict feed to OOS period
    # The BacktestFeed.stream() uses internal data, so we filter after load
    # Instead: reload with OOS only but pad with 200 lookback bars
    oos_start_idx = h1_full[h1_full["time"] >= OOS_START].index[0]
    lookback_idx = max(0, oos_start_idx - 200)
    h1_padded = h1_full.iloc[lookback_idx:].reset_index(drop=True)
    
    h4_oos_idx = h4_full[h4_full["time"] >= OOS_START].index[0] if len(h4_full[h4_full["time"] >= OOS_START]) > 0 else 0
    h4_lookback = max(0, h4_oos_idx - 100)
    h4_padded = h4_full.iloc[h4_lookback:].reset_index(drop=True)
    
    d1_oos_idx = d1_full[d1_full["time"] >= OOS_START].index[0] if len(d1_full[d1_full["time"] >= OOS_START]) > 0 else 0
    d1_lookback = max(0, d1_oos_idx - 50)
    d1_padded = d1_full.iloc[d1_lookback:].reset_index(drop=True)
    
    eng2 = _AblatedBacktestEngine(initial_balance=10000, ablation_flags=test.flags)
    eng2.load_df(INSTRUMENT, "H1", h1_padded)
    eng2.load_df(INSTRUMENT, "H4", h4_padded)
    eng2.load_df(INSTRUMENT, "D", d1_padded)
    
    r = eng2.run(INSTRUMENT, "H1")
    results.append(AblationResult(test=test, results=r))
    print(f"  → {r.total_trades} trades | Sharpe {r.sharpe_ratio:.2f} | WR {r.win_rate*100:.1f}% | MaxDD {r.max_drawdown_pct:.1f}% | PF {r.profit_factor:.2f}")

print_ablation_table(results, f"{INSTRUMENT} [OOS 2024-2026]")
