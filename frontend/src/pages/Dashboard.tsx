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
import { useMarketStore } from '../store'

const INSTRUMENTS = ['EUR_USD','GBP_USD','USD_JPY','AUD_USD','USD_CAD']
const TIMEFRAMES  = ['1h','4h']

export default function Dashboard() {
  const [pair, setPair] = useState('EUR_USD')
  const [tf,   setTf]   = useState('1h')  // default to H1 (M15 not stored)
  const [now,  setNow]  = useState(() => new Date())
  const { data: candles } = useCandles(pair, tf)
  const { data: equity  } = useEquityCurve()
  const { data: trades  } = useTradeJournal()
  const prices = useMarketStore(s => s.prices)
  const live = prices[pair]

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
        <div className="flex items-center gap-4 mb-4">
          <div className="flex gap-1">
            {INSTRUMENTS.map(i => (
              <button key={i} onClick={() => setPair(i)}
                className={`px-2 py-1 text-xs rounded ${pair===i ? 'bg-primary text-white' : 'bg-muted text-muted-foreground hover:text-foreground'}`}>
                {i.replace('_','/')}
              </button>
            ))}
          </div>
          <div className="flex gap-1 ml-auto">
            {TIMEFRAMES.map(t => (
              <button key={t} onClick={() => setTf(t)}
                className={`px-2 py-1 text-xs rounded ${tf===t ? 'bg-primary text-white' : 'bg-muted text-muted-foreground hover:text-foreground'}`}>
                {t}
              </button>
            ))}
          </div>
          {live && (
            <div className="text-xs font-mono ml-4">
              <span className="text-muted-foreground">Bid</span> {live.bid.toFixed(5)}
              <span className="text-muted-foreground ml-2">Ask</span> {live.ask.toFixed(5)}
              <span className="text-muted-foreground ml-2">Spread</span> {live.spread.toFixed(1)}
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