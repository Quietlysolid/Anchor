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

const INSTRUMENTS = ['EUR_USD', 'GBP_USD', 'USD_JPY']
const TIMEFRAMES  = ['15m','1h','4h']

// ── Session clock helpers ─────────────────────────────────────────────────────

type SessionInfo =
  | { active: true;  name: 'LONDON' | 'LCR' | 'ASIAN'; label: string; color: string; bgColor: string; closesIn: number }
  | { active: false; name: 'OFF' | 'WEEKEND';            label: string; nextName: string; opensIn: number }

function getSessionInfo(now: Date): SessionInfo {
  const dow  = now.getUTCDay()   // 0 = Sun, 6 = Sat
  const h    = now.getUTCHours()
  const m    = now.getUTCMinutes()
  const s    = now.getUTCSeconds()
  const tot  = h * 3600 + m * 60 + s   // seconds since midnight UTC
  const DAY  = 86400

  // Weekend (Sat full day, Sun before 21:00 — FX opens ~21:00 Sun UTC)
  if (dow === 6 || (dow === 0 && tot < 21 * 3600)) {
    const opensIn = dow === 6 ? (DAY - tot) + 21 * 3600 : 21 * 3600 - tot
    return { active: false, name: 'WEEKEND', label: 'Weekend', nextName: 'London (Mon)', opensIn }
  }

  // ASIAN: 00:00–03:00 UTC (JPY only but still worth showing)
  if (tot >= 0 && tot < 3 * 3600) {
    return { active: true, name: 'ASIAN', label: 'Asian', color: 'text-blue-400', bgColor: 'bg-blue-400/10', closesIn: 3 * 3600 - tot }
  }

  // LONDON: 07:15–12:00 UTC
  const londonStart = 7 * 3600 + 15 * 60
  const londonEnd   = 12 * 3600
  if (tot >= londonStart && tot < londonEnd) {
    return { active: true, name: 'LONDON', label: 'London', color: 'text-green-400', bgColor: 'bg-green-400/10', closesIn: londonEnd - tot }
  }

  // LCR: 17:00–20:00 UTC (suppressed Fri ≥18:00 by backend, show anyway)
  const lcrStart = 17 * 3600
  const lcrEnd   = 20 * 3600
  if (tot >= lcrStart && tot < lcrEnd) {
    return { active: true, name: 'LCR', label: 'LCR', color: 'text-amber-400', bgColor: 'bg-amber-400/10', closesIn: lcrEnd - tot }
  }

  // Off — compute next window
  let nextName: string, opensIn: number
  if (tot < londonStart) {
    // 03:00–07:14: waiting for London
    nextName = 'London'; opensIn = londonStart - tot
  } else if (tot < lcrStart) {
    // 12:00–16:59: waiting for LCR
    nextName = 'LCR'; opensIn = lcrStart - tot
  } else {
    // 20:00–23:59: waiting for Asian (next day 00:00) or effectively London
    nextName = 'Asian'; opensIn = DAY - tot
  }
  return { active: false, name: 'OFF', label: 'Off', nextName, opensIn }
}

function fmtDuration(secs: number): string {
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = secs % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

function SessionClock({ now }: { now: Date }) {
  const utc  = now.toISOString().slice(11, 19)   // HH:MM:SS
  const info = getSessionInfo(now)

  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="font-mono text-muted-foreground tabular-nums">{utc} UTC</span>

      {info.active ? (
        <>
          <span className={`flex items-center gap-1 font-semibold px-1.5 py-0.5 rounded ${info.color} ${info.bgColor}`}>
            <span className={`inline-block w-1.5 h-1.5 rounded-full ${info.color.replace('text-', 'bg-')} animate-pulse`} />
            {info.label}
          </span>
          <span className="text-muted-foreground tabular-nums">
            closes {fmtDuration(info.closesIn)}
          </span>
        </>
      ) : (
        <>
          <span className="font-semibold px-1.5 py-0.5 rounded text-muted-foreground bg-muted">
            {info.label}
          </span>
          <span className="text-muted-foreground tabular-nums">
            {info.nextName} in {fmtDuration(info.opensIn)}
          </span>
        </>
      )}
    </div>
  )
}

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
    <div className="p-4 md:p-6 space-y-4 md:space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Live Dashboard</h1>
        <SessionClock now={now} />
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
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
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
