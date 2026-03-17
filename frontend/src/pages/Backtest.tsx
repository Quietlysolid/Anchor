import { useState } from 'react'
import { api } from '../api/client'
import { Play, Loader2 } from 'lucide-react'
import { useWeightsStore } from '../store'

interface BacktestResult {
  instrument:               string
  start_date:               string
  end_date:                 string
  initial_balance:          number
  final_balance:            number
  total_trades:             number
  winning_trades:           number
  losing_trades:            number
  win_rate:                 number
  profit_factor:            number
  net_pnl:                  number
  net_pnl_pct:              number
  max_drawdown_pct:         number
  sharpe_ratio:             number
  avg_win_pips:             number
  avg_loss_pips:            number
  avg_trade_duration_hours: number
  largest_win:              number
  largest_loss:             number
  consecutive_wins:         number
  consecutive_losses:       number
}

type Status = 'good' | 'warn' | 'bad'

function statusColor(s: Status) {
  return s === 'good' ? 'text-green-400' : s === 'warn' ? 'text-amber-400' : 'text-red-400'
}

function Metric({ label, value, sub, status }: { label: string; value: string; sub?: string; status?: Status }) {
  return (
    <div className="bg-muted/50 rounded-lg p-3">
      <div className={`text-lg font-bold font-mono ${status ? statusColor(status) : ''}`}>{value}</div>
      <div className="text-xs font-medium mt-0.5">{label}</div>
      {sub && <div className="text-[10px] text-muted-foreground/50 mt-0.5 leading-relaxed">{sub}</div>}
    </div>
  )
}

export default function Backtest() {
  const [running,    setRunning]    = useState(false)
  const [result,     setResult]     = useState<BacktestResult | null>(null)
  const [error,      setError]      = useState<string | null>(null)
  const instruments = useWeightsStore(s => s.instruments)
  const t           = useWeightsStore(s => s.targets)
  const [instrument, setInstrument] = useState('EUR_USD')
  const [startDate,  setStartDate]  = useState('2020-01-01')
  const [endDate,    setEndDate]    = useState('2024-12-31')

  const run = async () => {
    setRunning(true); setError(null); setResult(null)
    try {
      const r = await api.post<BacktestResult>('/backtest/run', { instrument, start_date: startDate, end_date: endDate })
      setResult(r)
    } catch (e: any) {
      setError(e.message ?? 'Backtest failed — check that candle data is loaded for this pair and date range.')
    } finally {
      setRunning(false)
    }
  }

  // Verdict: how many of the key metrics passed?
  const verdict = result ? (() => {
    const passed = [
      result.win_rate      >= t.win_rate_good,
      result.profit_factor >= t.profit_factor_good,
      result.sharpe_ratio  >= t.sharpe_good,
      result.max_drawdown_pct <= t.drawdown_halt,
    ]
    const count = passed.filter(Boolean).length
    if (count === 4) return { label: 'All targets met', color: 'text-green-400 bg-green-400/10 border-green-400/20' }
    if (count >= 2) return { label: `${count} of 4 targets met`, color: 'text-amber-400 bg-amber-400/10 border-amber-400/20' }
    return { label: `Only ${count} of 4 targets met`, color: 'text-red-400 bg-red-400/10 border-red-400/20' }
  })() : null

  return (
    <div className="p-4 md:p-6 space-y-4 md:space-y-6 max-w-2xl">
      <div>
        <h1 className="text-xl font-bold">Historical Test</h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Replay the strategy on past data to see how it would have performed.
        </p>
      </div>

      {/* Controls */}
      <div className="bg-card border border-border rounded-lg p-5 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <label htmlFor="bt-instrument" className="text-xs font-medium mb-1.5 block">Currency pair</label>
            <select
              id="bt-instrument"
              value={instrument}
              onChange={e => setInstrument(e.target.value)}
              className="w-full bg-muted border border-border rounded-lg px-2.5 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary/50 transition-colors"
            >
              {instruments.map(i => (
                <option key={i} value={i}>{i.replace('_', '/')}</option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="bt-start" className="text-xs font-medium mb-1.5 block">From</label>
            <input id="bt-start" type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
              className="w-full bg-muted border border-border rounded-lg px-2.5 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary/50 transition-colors" />
          </div>
          <div>
            <label htmlFor="bt-end" className="text-xs font-medium mb-1.5 block">To</label>
            <input id="bt-end" type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
              className="w-full bg-muted border border-border rounded-lg px-2.5 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary/50 transition-colors" />
          </div>
        </div>

        <button onClick={run} disabled={running}
          className="w-full flex items-center justify-center gap-2 bg-primary text-white py-2.5 rounded-lg text-sm font-medium hover:bg-primary/90 active:bg-primary/80 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-150"
        >
          {running
            ? <><Loader2 size={14} className="animate-spin" /> Running…</>
            : <><Play size={14} /> Run Test</>
          }
        </button>
      </div>

      {error && (
        <div className="bg-red-400/10 border border-red-400/30 rounded-lg p-4 text-sm text-red-400">{error}</div>
      )}

      {result && (
        <div className="bg-card border border-border rounded-lg p-5 animate-fade-in space-y-4">

          {/* Header + verdict */}
          <div className="flex items-start justify-between gap-4">
            <div>
              <h3 className="text-sm font-semibold">{result.instrument.replace('_', '/')} — {result.start_date} to {result.end_date}</h3>
              <p className="text-[11px] text-muted-foreground/60 mt-0.5">
                Started with ${result.initial_balance.toLocaleString()} · ended with{' '}
                <span className={result.final_balance >= result.initial_balance ? 'text-green-400' : 'text-red-400'}>
                  ${result.final_balance.toFixed(2)}
                </span>
              </p>
            </div>
            {verdict && (
              <span className={`text-xs font-medium px-2.5 py-1.5 rounded-lg border shrink-0 ${verdict.color}`}>
                {verdict.label}
              </span>
            )}
          </div>

          {/* Key metrics */}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
            <Metric
              label="Trades won"
              value={`${(result.win_rate * 100).toFixed(1)}%`}
              sub={`${result.winning_trades}W · ${result.losing_trades}L of ${result.total_trades} total`}
              status={result.win_rate >= t.win_rate_good ? 'good' : result.win_rate >= t.win_rate_warn ? 'warn' : 'bad'}
            />
            <Metric
              label="Wins vs losses"
              value={`${result.profit_factor.toFixed(2)}×`}
              sub={`Won $${result.profit_factor.toFixed(2)} for every $1 lost`}
              status={result.profit_factor >= t.profit_factor_good ? 'good' : result.profit_factor >= t.profit_factor_warn ? 'warn' : 'bad'}
            />
            <Metric
              label="Net profit"
              value={`${result.net_pnl >= 0 ? '+' : ''}$${result.net_pnl.toFixed(0)}`}
              sub={`${result.net_pnl_pct >= 0 ? '+' : ''}${(result.net_pnl_pct * 100).toFixed(1)}% on starting balance`}
              status={result.net_pnl > 0 ? 'good' : result.net_pnl === 0 ? 'warn' : 'bad'}
            />
            <Metric
              label="Biggest dip"
              value={`${(result.max_drawdown_pct * 100).toFixed(1)}%`}
              sub={`Halt limit is ${Math.round(t.drawdown_halt * 100)}%`}
              status={result.max_drawdown_pct <= t.drawdown_good ? 'good' : result.max_drawdown_pct <= t.drawdown_halt ? 'warn' : 'bad'}
            />
            <Metric
              label="Return smoothness"
              value={result.sharpe_ratio.toFixed(2)}
              sub={`Target ${t.sharpe_good}+ · higher = steadier gains`}
              status={result.sharpe_ratio >= t.sharpe_good ? 'good' : result.sharpe_ratio >= t.sharpe_warn ? 'warn' : 'bad'}
            />
            <Metric
              label="Avg trade duration"
              value={result.avg_trade_duration_hours < 24
                ? `${result.avg_trade_duration_hours.toFixed(1)}h`
                : `${(result.avg_trade_duration_hours / 24).toFixed(1)}d`}
              sub="How long the average trade was open"
            />
          </div>

          {/* Trade detail */}
          <div className="pt-3 border-t border-border grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Avg win</span>
              <span className="font-mono text-green-400">+${result.avg_win_pips.toFixed(2)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Avg loss</span>
              <span className="font-mono text-red-400">−${Math.abs(result.avg_loss_pips).toFixed(2)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Best trade</span>
              <span className="font-mono text-green-400">+${result.largest_win.toFixed(2)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Worst trade</span>
              <span className="font-mono text-red-400">−${Math.abs(result.largest_loss).toFixed(2)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Max win streak</span>
              <span className="font-mono">{result.consecutive_wins}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Max loss streak</span>
              <span className="font-mono">{result.consecutive_losses}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
