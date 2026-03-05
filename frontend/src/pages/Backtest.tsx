import { useState } from 'react'
import { api } from '../api/client'

interface BacktestResult {
  total_trades: number
  win_rate: number
  sharpe_ratio: number
  max_drawdown_pct: number
  net_pnl: number
  profit_factor: number
  start_date: string
  end_date: string
}

export default function Backtest() {
  const [running, setRunning] = useState(false)
  const [result,  setResult]  = useState<BacktestResult | null>(null)
  const [error,   setError]   = useState<string | null>(null)
  const [instrument, setInstrument] = useState('EUR_USD')
  const [startDate,  setStartDate]  = useState('2020-01-01')
  const [endDate,    setEndDate]    = useState('2024-12-31')

  const run = async () => {
    setRunning(true); setError(null); setResult(null)
    try {
      const r = await api.post<BacktestResult>('/backtest/run', { instrument, start_date: startDate, end_date: endDate })
      setResult(r)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="p-6 space-y-6 max-w-2xl">
      <h1 className="text-xl font-bold">Backtest Engine</h1>

      <div className="bg-card border border-border rounded-lg p-5 space-y-4">
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label htmlFor="bt-instrument" className="text-xs text-muted-foreground mb-1 block">Instrument</label>
            <select id="bt-instrument" title="Instrument" value={instrument} onChange={e => setInstrument(e.target.value)}
              className="w-full bg-muted border border-border rounded px-2 py-1.5 text-sm text-foreground">
              {['EUR_USD','GBP_USD','USD_JPY','AUD_USD','USD_CAD'].map(i => (
                <option key={i} value={i}>{i.replace('_','/')}</option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="bt-start-date" className="text-xs text-muted-foreground mb-1 block">Start Date</label>
            <input id="bt-start-date" title="Start Date" type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
              className="w-full bg-muted border border-border rounded px-2 py-1.5 text-sm text-foreground" />
          </div>
          <div>
            <label htmlFor="bt-end-date" className="text-xs text-muted-foreground mb-1 block">End Date</label>
            <input id="bt-end-date" title="End Date" type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
              className="w-full bg-muted border border-border rounded px-2 py-1.5 text-sm text-foreground" />
          </div>
        </div>

        <button onClick={run} disabled={running}
          className="w-full bg-primary text-white py-2 rounded text-sm font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed">
          {running ? 'Running backtest…' : 'Run Backtest'}
        </button>
      </div>

      {error && (
        <div className="bg-red-400/10 border border-red-400/30 rounded-lg p-4 text-sm text-red-400">{error}</div>
      )}

      {result && (
        <div className="bg-card border border-border rounded-lg p-5">
          <h3 className="text-sm font-semibold mb-4">Results — {result.start_date} to {result.end_date}</h3>
          <div className="grid grid-cols-3 gap-4 text-center">
            {[
              { label: 'Total Trades', value: String(result.total_trades) },
              { label: 'Win Rate',     value: `${(result.win_rate * 100).toFixed(1)}%` },
              { label: 'Sharpe',       value: result.sharpe_ratio.toFixed(2) },
              { label: 'Max Drawdown', value: `${(result.max_drawdown_pct * 100).toFixed(1)}%` },
              { label: 'Profit Factor',value: result.profit_factor.toFixed(2) },
              { label: 'Net P&L',      value: `$${result.net_pnl.toFixed(2)}` },
            ].map(({ label, value }) => (
              <div key={label} className="bg-muted rounded-lg p-3">
                <div className="text-lg font-bold font-mono">{value}</div>
                <div className="text-xs text-muted-foreground mt-0.5">{label}</div>
              </div>
            ))}
          </div>

          <div className="mt-4 text-xs text-muted-foreground">
            Requires ≥ 1,000 trades and Sharpe &gt; 1.0 before going live.
          </div>
        </div>
      )}
    </div>
  )
}