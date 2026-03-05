"""
Fetch 3 years of OANDA historical data and run the backtest engine
on all 5 instruments. Prints a full results table.

Run on VPS:
    docker exec anchor_engine python /app/run_backtest.py
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone, timedelta

import oandapyV20
import oandapyV20.endpoints.instruments as instruments_ep
import pandas as pd

# ── bootstrap the anchor package ──────────────────────────────────────────────
import os
sys.path.insert(0, "/app")
os.environ.setdefault("PYTHONPATH", "/app")

from anchor.config import get_settings
from anchor.backtesting.engine import BacktestEngine

settings = get_settings()

INSTRUMENTS = settings.instruments          # ["EUR_USD","GBP_USD","USD_JPY","AUD_USD","USD_CAD"]
TIMEFRAMES   = ["H1", "H4", "D"]
START_DATE   = datetime(2022, 1, 1, tzinfo=timezone.utc)
CANDLES_PER_REQUEST = 5000                  # OANDA max per request
INITIAL_BALANCE     = 10_000.0             # backtest starting capital


# ── OANDA candle fetcher ───────────────────────────────────────────────────────

def fetch_candles(client, instrument: str, granularity: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Pull all candles between start and end, handling pagination."""
    all_rows = []
    current = start

    tf_hours = {"H1": 1, "H4": 4, "D": 24}
    step_hours = tf_hours.get(granularity, 1) * CANDLES_PER_REQUEST

    while current < end:
        chunk_end = min(current + timedelta(hours=step_hours), end)
        params = {
            "granularity": granularity,
            "from": current.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to":   chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": CANDLES_PER_REQUEST,
            "price": "M",   # midpoint
        }
        try:
            ep = instruments_ep.InstrumentsCandles(instrument, params=params)
            client.request(ep)
            candles = ep.response.get("candles", [])
            for c in candles:
                if c.get("complete"):
                    mid = c["mid"]
                    all_rows.append({
                        "time":   c["time"],
                        "open":   float(mid["o"]),
                        "high":   float(mid["h"]),
                        "low":    float(mid["l"]),
                        "close":  float(mid["c"]),
                        "volume": int(c.get("volume", 0)),
                    })
        except Exception as exc:
            print(f"  [warn] {instrument} {granularity} chunk failed: {exc}")

        current = chunk_end
        time.sleep(0.3)  # rate limit respect

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    return df


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    client = oandapyV20.API(
        access_token=settings.oanda_api_key,
        environment=settings.oanda_environment,
    )
    end = datetime.now(timezone.utc)

    print(f"\n{'='*70}")
    print(f"  ANCHOR BACKTEST  |  {START_DATE.date()} → {end.date()}  |  ${INITIAL_BALANCE:,.0f} starting")
    print(f"{'='*70}\n")

    all_results = []

    for instrument in INSTRUMENTS:
        print(f"── {instrument} ──────────────────────────────────────────────────")

        # Fetch all timeframes
        data = {}
        for tf in TIMEFRAMES:
            print(f"  Fetching {tf}...", end="", flush=True)
            df = fetch_candles(client, instrument, tf, START_DATE, end)
            print(f" {len(df)} candles")
            data[tf] = df

        if data["H1"].empty or len(data["H1"]) < 200:
            print(f"  [skip] Insufficient H1 data\n")
            continue

        # Load into engine
        eng = BacktestEngine(initial_balance=INITIAL_BALANCE)
        eng.load_df(instrument, "H1", data["H1"])
        if not data["H4"].empty:
            eng.load_df(instrument, "H4", data["H4"])
        if not data["D"].empty:
            eng.load_df(instrument, "D", data["D"])

        print(f"  Running backtest...", end="", flush=True)
        try:
            r = eng.run(instrument, "H1")
        except Exception as exc:
            print(f" FAILED: {exc}\n")
            continue
        print(f" done — {r.total_trades} trades\n")

        all_results.append(r)

    # ── Summary table ────────────────────────────────────────────────────────
    if not all_results:
        print("No results — check data fetch or signal conditions.")
        return

    print(f"\n{'='*70}")
    print(f"  RESULTS SUMMARY")
    print(f"{'='*70}")
    hdr = f"{'Pair':<12} {'Trades':>7} {'WinRate':>8} {'PF':>6} {'NetP&L':>10} {'MaxDD':>8} {'Sharpe':>8} {'Sortino':>8}"
    print(hdr)
    print("-" * 70)

    for r in all_results:
        verdict = ""
        if r.total_trades < 30:
            verdict = "⚠ LOW TRADES"
        elif r.sharpe_ratio >= 1.0 and r.win_rate >= 0.40:
            verdict = "✓ PASS"
        else:
            verdict = "✗ FAIL"

        print(
            f"{r.instrument:<12} "
            f"{r.total_trades:>7} "
            f"{r.win_rate*100:>7.1f}% "
            f"{r.profit_factor:>6.2f} "
            f"${r.net_pnl:>9,.0f} "
            f"{r.max_drawdown_pct:>7.1f}% "
            f"{r.sharpe_ratio:>8.2f} "
            f"{r.sortino_ratio:>8.2f}  "
            f"{verdict}"
        )

    print(f"\n{'='*70}")
    print("Minimum to go live: Sharpe >= 1.0, Win Rate >= 40%, Trades >= 100")
    print(f"{'='*70}\n")

    # Print trade log for any passing pairs
    for r in all_results:
        if r.sharpe_ratio >= 1.0 and r.total_trades >= 30:
            print(f"\nSample trades — {r.instrument}:")
            for t in r.trade_log[:10]:
                pl_str = f"+${t['net_pl']:.2f}" if t['net_pl'] > 0 else f"-${abs(t['net_pl']):.2f}"
                print(f"  {t['entry_time'][:16]}  {t['direction']:<5}  {pl_str:>10}  [{t['close_reason']}]")


if __name__ == "__main__":
    main()
