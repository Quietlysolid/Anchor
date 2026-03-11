import { useState, useEffect } from 'react'
import { CandlestickChart } from '../components/charts/CandlestickChart'
import { EquityCurveChart } from '../components/charts/EquityCurveChart'
import { SignalScoringPanel } from '../components/panels/SignalScoringPanel'
import { RegimePanel } from '../components/panels/RegimePanel'
import { RiskMetricsPanel } from '../components/panels/RiskMetricsPanel'
import { SystemHealthPanel } from '../components/panels/SystemHealthPanel'
import { EconomicCalendarPanel } from '../components/panels/EconomicCalendarPanel'
import { PositionsTable } from '../components/tables/PositionTable'
import { useCandles, useEquityCurve, useTradeJournal } from '../api/hooks'
import { useMarketStore, useSignalStore } from '../store'

// USD_CAD removed — OOS PF 0.98, disabled until live data proves PF > 1.05
const INSTRUMENTS = ['EUR_USD','GBP_USD','USD_JPY','AUD_USD','NZD_USD','USD_CHF','EUR_GBP','GBP_JPY']
const TIMEFRAMES  = ['15m','1h','4h']

// Compact badge showing a macro score value
function MacroBadge({ label, score }: { label: string; score: number | null }) {
  if (score === null) return null
  const pct = Math.round(score * 100)
  const color = pct >= 65 ? 'text-green-400 bg-green-400/10' : pct <= 35 ? 'text-red-400 bg-red-400/10' : 'text-muted-foreground bg-muted'
  return (
    <span className={`text-xs rounded px-1.5 py-0.5 font-mono ${color}`}>
      {label} {pct}%
    </span>
  )
}

export default function Dashboard() {
  const [pair, setPair] = useState('EUR_USD')
  const [tf,   setTf]   = useState('1h')
  const [now,  setNow]  = useState(() => new Date())
  const { data: candles } = useCandles(pair, tf)
  const { data: equity  } = useEquityCurve()
  const { data: trades  } = useTradeJournal()
  const prices  = useMarketStore(s => s.prices)
  const signals = useSignalStore(s => s.signals)
  const live    = prices[pair]

  // Latest signal for the selected pair (any timeframe)
  const latestSignal = signals.find(s => s.instrument === pair)

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Live Dashboard</h1>
        <div className="text-xs text-muted-foreground">{now.toLocaleString()}</div>
      </div>

      {/* Chart + controls */}
      <div className="bg-card border border-border rounded-lg p-4">
        {/* Instrument row */}
        <div className="flex flex-wrap items-center gap-1 mb-2">
          {INSTRUMENTS.map(i => (
            <button type="button" key={i} onClick={() => setPair(i)}
              className={`px-2 py-1 text-xs rounded ${pair===i ? 'bg-primary text-white' : 'bg-muted text-muted-foreground hover:text-foreground'}`}>
              {i.replace('_','/')}
            </button>
          ))}
          <div className="flex gap-1 ml-auto">
            {TIMEFRAMES.map(t => (
              <button type="button" key={t} onClick={() => setTf(t)}
                className={`px-2 py-1 text-xs rounded ${tf===t ? 'bg-primary text-white' : 'bg-muted text-muted-foreground hover:text-foreground'}`}>
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* Live price + macro signal strip */}
        <div className="flex items-center gap-3 mb-3 min-h-[22px]">
          {live && (
            <div className="text-xs font-mono">
              <span className="text-muted-foreground">Bid</span> {live.bid.toFixed(5)}
              <span className="text-muted-foreground ml-2">Ask</span> {live.ask.toFixed(5)}
              <span className="text-muted-foreground ml-2">Spread</span> {live.spread.toFixed(1)}
            </div>
          )}
          {latestSignal && !latestSignal.suppressed && (
            <div className="flex items-center gap-1.5 ml-auto">
              <span className={`text-xs font-bold ${latestSignal.direction === 'LONG' ? 'text-green-400' : 'text-red-400'}`}>
                {latestSignal.direction}
              </span>
              <span className="text-xs text-muted-foreground">
                {Math.round((latestSignal.confluence_score ?? 0) * 100)}%
              </span>
              <MacroBadge label="COT"  score={latestSignal.cot_score ?? null} />
              <MacroBadge label="Rate" score={latestSignal.rate_divergence_score ?? null} />
              <span className="text-xs text-muted-foreground">{latestSignal.timeframe}</span>
            </div>
          )}
          {latestSignal?.suppressed && (
            <div className="ml-auto text-xs text-amber-400/70">
              {latestSignal.suppression_reason}
            </div>
          )}
        </div>

        <CandlestickChart
          key={`${pair}-${tf}`}
          candles={candles ?? []}
          instrument={pair}
          trades={(trades ?? []).filter(t => t.instrument === pair)}
          height={280}
        />
      </div>

      {/* Panels row */}
      <div className="grid grid-cols-4 gap-4">
        <SignalScoringPanel />
        <RegimePanel />
        <RiskMetricsPanel />
        <SystemHealthPanel />
      </div>

      {/* Economic calendar */}
      <EconomicCalendarPanel />

      {/* Equity curve */}
      <div className="bg-card border border-border rounded-lg p-4">
        <h3 className="text-sm font-semibold mb-3">Equity Curve</h3>
        <EquityCurveChart data={equity ?? []} />
      </div>

      {/* Open positions */}
      <PositionsTable />
    </div>
  )
}
