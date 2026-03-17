import { useState, useEffect } from 'react'
import { CandlestickChart } from '../components/charts/CandlestickChart'
import { SignalScoringPanel } from '../components/panels/SignalScoringPanel'
import { RegimePanel } from '../components/panels/RegimePanel'
import { RiskMetricsPanel } from '../components/panels/RiskMetricsPanel'
import { EconomicCalendarPanel } from '../components/panels/EconomicCalendarPanel'
import { MarketContextPanel } from '../components/panels/MarketContextPanel'
import { CorrelationPanel } from '../components/panels/CorrelationPanel'
import { EdgeConfidencePanel } from '../components/panels/EdgeConfidencePanel'
import { IntelligenceBriefPanel } from '../components/panels/IntelligenceBriefPanel'
import { PositionsTable } from '../components/tables/PositionTable'
import { useCandles, useTradeJournal } from '../api/hooks'
import { useMarketStore, useSignalStore, useWeightsStore } from '../store'
import { suppressionText } from '../utils/session'

const TIMEFRAMES = ['15m', '1h', '4h', '1d']

// ── Session clock helpers ─────────────────────────────────────────────────────

type SessionInfo =
  | { active: true;  name: 'LONDON' | 'LCR' | 'ASIAN'; label: string; color: string; bgColor: string; closesIn: number }
  | { active: false; name: 'OFF' | 'WEEKEND';            label: string; nextName: string; opensIn: number }

function getSessionInfo(now: Date): SessionInfo {
  const dow  = now.getUTCDay()
  const h    = now.getUTCHours()
  const m    = now.getUTCMinutes()
  const s    = now.getUTCSeconds()
  const tot  = h * 3600 + m * 60 + s
  const DAY  = 86400

  if (dow === 6 || (dow === 0 && tot < 21 * 3600)) {
    const opensIn = dow === 6 ? (DAY - tot) + 21 * 3600 : 21 * 3600 - tot
    return { active: false, name: 'WEEKEND', label: 'Weekend', nextName: 'London (Mon)', opensIn }
  }
  if (tot >= 0 && tot < 3 * 3600) {
    return { active: true, name: 'ASIAN', label: 'Asian', color: 'text-blue-400', bgColor: 'bg-blue-400/10', closesIn: 3 * 3600 - tot }
  }
  const londonStart = 7 * 3600 + 15 * 60
  const londonEnd   = 12 * 3600
  if (tot >= londonStart && tot < londonEnd) {
    return { active: true, name: 'LONDON', label: 'London', color: 'text-green-400', bgColor: 'bg-green-400/10', closesIn: londonEnd - tot }
  }
  const lcrStart = 17 * 3600
  const lcrEnd   = 20 * 3600
  if (tot >= lcrStart && tot < lcrEnd) {
    return { active: true, name: 'LCR', label: 'LCR', color: 'text-amber-400', bgColor: 'bg-amber-400/10', closesIn: lcrEnd - tot }
  }
  let nextName: string, opensIn: number
  if (tot < londonStart) { nextName = 'London'; opensIn = londonStart - tot }
  else if (tot < lcrStart) { nextName = 'LCR'; opensIn = lcrStart - tot }
  else { nextName = 'Asian'; opensIn = DAY - tot }
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
  const localTime = now.toLocaleTimeString('en-US', {
    timeZone: 'America/New_York',
    hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true,
  })
  const info = getSessionInfo(now)
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="font-mono text-muted-foreground tabular-nums">{localTime} ET</span>
      {info.active ? (
        <>
          <span className={`flex items-center gap-1 font-semibold px-1.5 py-0.5 rounded ${info.color} ${info.bgColor}`}>
            <span className={`inline-block w-1.5 h-1.5 rounded-full ${info.color.replace('text-', 'bg-')} animate-pulse`} />
            {info.label}
          </span>
          <span className="text-muted-foreground tabular-nums">closes {fmtDuration(info.closesIn)}</span>
        </>
      ) : (
        <>
          <span className="font-semibold px-1.5 py-0.5 rounded text-muted-foreground bg-muted">{info.label}</span>
          <span className="text-muted-foreground tabular-nums">{info.nextName} in {fmtDuration(info.opensIn)}</span>
        </>
      )}
    </div>
  )
}

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
  const { data: trades  } = useTradeJournal()
  const prices      = useMarketStore(s => s.prices)
  const signals     = useSignalStore(s => s.signals)
  const instruments = useWeightsStore(s => s.instruments)
  const live        = prices[pair]
  const latestSignal = signals.find(s => s.instrument === pair)

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="p-4 md:p-6 space-y-4 md:space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Live Dashboard</h1>
        <SessionClock now={now} />
      </div>

      {/* ── Pair selector — governs both chart and signal breakdown ── */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[11px] text-muted-foreground/50 mr-0.5">Pair</span>
        {instruments.map(i => (
          <button type="button" key={i} onClick={() => setPair(i)}
            className={`px-2.5 py-1 text-xs rounded-md font-medium transition-all duration-100
              ${pair === i
                ? 'bg-primary text-white shadow-sm'
                : 'bg-muted text-muted-foreground hover:text-foreground hover:bg-muted/80'
              }`}>
            {i.replace('_', '/')}
          </button>
        ))}
      </div>

      {/* ── Top section: Chart + Signal panel side-by-side ── */}
      <div className="grid grid-cols-1 xl:grid-cols-5 gap-4">
        {/* Chart card — takes 3/5 on xl */}
        <div className="xl:col-span-3 bg-card border border-border rounded-lg p-4">
          {/* Timeframe selector */}
          <div className="flex gap-1 mb-2 justify-end">
            {TIMEFRAMES.map(t => (
              <button type="button" key={t} onClick={() => setTf(t)}
                className={`px-2.5 py-1 text-xs rounded-md font-medium transition-all duration-100
                  ${tf === t
                    ? 'bg-primary text-white shadow-sm'
                    : 'bg-muted text-muted-foreground hover:text-foreground hover:bg-muted/80'
                  }`}>
                {t}
              </button>
            ))}
          </div>

          {/* Live price + signal strip */}
          <div className="flex items-center gap-3 mb-3 min-h-[24px]">
            {live ? (
              <div className="text-xs font-mono text-muted-foreground">
                <span className="text-foreground font-medium">{live.bid.toFixed(live.bid > 10 ? 3 : 5)}</span>
                <span className="mx-1.5 opacity-40">/</span>
                <span className="text-foreground font-medium">{live.ask.toFixed(live.ask > 10 ? 3 : 5)}</span>
                <span className="ml-2 text-[11px] opacity-60">spread {live.spread.toFixed(1)}</span>
              </div>
            ) : (
              <div className="skeleton h-4 w-40" />
            )}
            {latestSignal && !latestSignal.suppressed && (
              <div className="flex items-center gap-1.5 ml-auto animate-fade-in">
                <span className={`text-xs font-bold px-2 py-0.5 rounded-md ${
                  latestSignal.direction === 'LONG' ? 'text-green-400 bg-green-400/10' : 'text-red-400 bg-red-400/10'
                }`}>
                  {latestSignal.direction === 'LONG' ? '↑ Buy' : '↓ Sell'}
                </span>
                <span className="text-xs text-muted-foreground font-mono">
                  {Math.round((latestSignal.confluence_score ?? 0) * 100)}%
                </span>
                <MacroBadge label="Positioning" score={latestSignal.cot_score ?? null} />
                <MacroBadge label="Rates"       score={latestSignal.rate_divergence_score ?? null} />
                <span className="text-xs text-muted-foreground">{latestSignal.timeframe}</span>
              </div>
            )}
            {latestSignal?.suppressed && (
              <div className="ml-auto text-[11px] text-muted-foreground/60 italic">
                {suppressionText(latestSignal.suppression_reason)}
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
          <div className="flex items-center gap-4 mt-2 text-[10px] text-muted-foreground/50">
            <span className="flex items-center gap-1.5"><span className="text-blue-400 font-bold">▲</span>Trade entry</span>
            <span className="flex items-center gap-1.5"><span className="text-green-400 text-xs">●</span>Closed in profit</span>
            <span className="flex items-center gap-1.5"><span className="text-red-400 text-xs">●</span>Closed at a loss</span>
          </div>
        </div>

        {/* Signal scoring — takes 2/5 on xl */}
        <div className="xl:col-span-2">
          <SignalScoringPanel instrument={pair} />
        </div>
      </div>

      {/* ── Open positions ── */}
      <PositionsTable />

      {/* ── 3-panel info row: Regime · Scorecard · Calendar ── */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <RegimePanel />
        <RiskMetricsPanel />
        <EconomicCalendarPanel />
      </div>

      {/* ── Macro context + Correlation ── */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <MarketContextPanel />
        <CorrelationPanel />
      </div>

      {/* ── Edge confidence ── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <EdgeConfidencePanel />
        <div className="bg-card rounded-lg p-4 border border-border">
          <h3 className="text-sm font-semibold mb-2">What This Means</h3>
          <div className="space-y-2 text-[11px] text-muted-foreground/70 leading-relaxed">
            <p><span className="text-green-400 font-semibold">HIGH</span> — Session structure is directional, pairs correlate normally, macro is calm. Trade the system as designed.</p>
            <p><span className="text-amber-400 font-semibold">REDUCED</span> — One signal is flagged. Edge likely still holds but something in the environment is shifting. Consider 75% size and closer monitoring.</p>
            <p><span className="text-red-400 font-semibold">LOW</span> — Two or more signals flagged. A macro driver may be overriding session mechanics. Review what's happening before the next trading day — don't pause automatically, but understand why.</p>
          </div>
        </div>
      </div>

      {/* ── AI Co-Pilot brief ── */}
      <IntelligenceBriefPanel />

    </div>
  )
}
