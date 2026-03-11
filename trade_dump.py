import sys
sys.path.insert(0, '/app')
import pandas as pd
from anchor.backtesting.engine import BacktestEngine

eng = BacktestEngine(initial_balance=10000)

def load_slice(path, start, end=None):
    df = pd.read_csv(path, parse_dates=['time'])
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df = df.sort_values('time').reset_index(drop=True)
    if start:
        df = df[df['time'] >= start]
    if end:
        df = df[df['time'] <= end]
    return df.reset_index(drop=True)

h1 = load_slice('/tmp/data/EUR_USD_H1.csv', '2023-12-31')
h4 = load_slice('/tmp/data/EUR_USD_H4.csv', '2023-12-31')
d1 = load_slice('/tmp/data/EUR_USD_D.csv', '2023-12-31')

eng.load_df('EUR_USD', 'H1', h1)
eng.load_df('EUR_USD', 'H4', h4)
eng.load_df('EUR_USD', 'D', d1)

results = eng.run('EUR_USD', 'H1')
print(f'Trades: {results.total_trades}, WR: {results.win_rate*100:.1f}%, PF: {results.profit_factor:.2f}, Net: {results.net_pnl_pct:.2f}%')
wins = [t for t in results.trade_log if t.get('net_pl', 0) > 0]
losses = [t for t in results.trade_log if t.get('net_pl', 0) <= 0]
print(f'Avg win PnL: {sum(t["net_pl"] for t in wins)/len(wins) if wins else 0:.2f}')
print(f'Avg loss PnL: {sum(t["net_pl"] for t in losses)/len(losses) if losses else 0:.2f}')
print('\nTrades:')
for t in results.trade_log:
    ep = t.get('entry_price', 0)
    xp = t.get('exit_price', 0)
    pnl = t.get('net_pl', 0)
    reason = t.get('close_reason', '')
    entry_time = t.get('entry_time', '')[:10]
    print(f"  {t['direction']:5} {entry_time} entry={ep:.5f} exit={xp:.5f} pnl={pnl:+.2f} {reason}")
