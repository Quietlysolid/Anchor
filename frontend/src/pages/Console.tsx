import { useMemo, useState, useEffect } from 'react'
import { GlowCard } from '../components/ui/GlowCard'
import { AnimatedNumber } from '../components/ui/AnimatedNumber'
import { EquityCurve } from '../components/charts/EquityCurve'
import { AIBriefPanel } from '../components/panels/AIBriefPanel'
import { PositionsTable } from '../components/panels/PositionsTable'
import { SessionTimeline } from '../components/ui/SessionTimeline'
import { useEquityCurve, useIntelligenceBrief } from '../api/hooks'
import { usePositionStore, useSystemStore } from '../store'
import { getPhaseInfo } from '../utils/session'

interface StatProps { label: string; value: string; color?: string }

function Stat({ label, value, color = 'text-anchor-text' }: StatProps) {
  return (
    <div>
      <p className="text-[10px] text-anchor-muted tracking-[0.15em] uppercase mb-1">{label}</p>
      <p className={`text-lg font-mono font-semibold tabular-nums ${color}`}>{value}</p>
    </div>
  )
}

export default function Console() {
  const { data: equityData }   = useEquityCurve()
  const { data: brief }        = useIntelligenceBrief()
  const { equity }             = useSystemStore()
  const positions              = usePositionStore(s => s.positions)

  const equityPts = equityData ?? []

  // Session-aware border
  const [sessionNow, setSessionNow] = useState(new Date())
  useEffect(() => {
    const t = setInterval(() => setSessionNow(new Date()), 60_000)
    return () => clearInterval(t)
  }, [])
  const utcMins = sessionNow.getUTCHours() * 60 + sessionNow.getUTCMinutes()
  const { phase } = getPhaseInfo(utcMins)
  const sessionBorder =
    phase === 'london' ? 'border-t-2 border-t-amber-500/30' :
    phase === 'lcr'    ? 'border-t-2 border-t-sky-500/30'   : ''

  const latestPt = equityPts[equityPts.length - 1]
  const firstPt  = equityPts[0]
  const equityVal = equity > 0 ? equity : (latestPt?.account_equity ?? 0)

  // Today's P&L: equity now vs last snapshot from a previous ET calendar day
  const todayPL = useMemo(() => {
    if (!equityVal || !equityPts.length) return null
    const etToday = new Date().toLocaleDateString('en-US', { timeZone: 'America/New_York' })
    const prevPt  = [...equityPts].reverse().find(p =>
      new Date(p.time).toLocaleDateString('en-US', { timeZone: 'America/New_York' }) !== etToday
    )
    return prevPt != null ? equityVal - prevPt.account_equity : null
  }, [equityPts, equityVal])

  const totalGainPct = firstPt && equityVal
    ? ((equityVal - firstPt.account_equity) / firstPt.account_equity) * 100
    : 0

  const maxDD = useMemo(() => {
    let peak = -Infinity, maxDd = 0
    for (const pt of equityPts) {
      if (pt.account_equity > peak) peak = pt.account_equity
      const dd = (peak - pt.account_equity) / peak
      if (dd > maxDd) maxDd = dd
    }
    return maxDd
  }, [equityPts])

  const todayColor = todayPL == null ? 'text-anchor-muted' : todayPL >= 0 ? 'text-anchor-green' : 'text-anchor-red'
  const todayPrefix = todayPL == null ? '' : todayPL >= 0 ? '+$' : '−$'

  return (
    <div className="min-h-screen bg-anchor-void p-5 space-y-4">

      {/* Session strip */}
      <SessionTimeline />

      {/* Hero card */}
      <GlowCard padding={false} className={`p-6 ${sessionBorder}`}>

        {/* Top row: hero P&L + secondary stats */}
        <div className="flex flex-col md:flex-row md:items-start gap-6 mb-6">

          {/* Today's P&L — the one number that matters */}
          <div>
            <p className="text-[10px] text-anchor-muted tracking-[0.2em] uppercase mb-2">Today</p>
            {todayPL != null ? (
              <AnimatedNumber
                value={Math.abs(todayPL)}
                prefix={todayPrefix}
                decimals={2}
                className={`text-5xl font-semibold font-mono tabular-nums leading-none ${todayColor}`}
              />
            ) : (
              <span className="text-5xl font-semibold font-mono tabular-nums leading-none text-anchor-muted">—</span>
            )}
          </div>

          {/* Secondary stats */}
          <div className="grid grid-cols-3 gap-x-8 gap-y-3 md:ml-auto md:pt-1.5">
            <Stat
              label="Portfolio"
              value={equityVal > 0 ? `$${equityVal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'}
            />
            <Stat
              label="All Time"
              value={firstPt ? `${totalGainPct >= 0 ? '+' : ''}${totalGainPct.toFixed(2)}%` : '—'}
              color={totalGainPct >= 0 ? 'text-anchor-green' : 'text-anchor-red'}
            />
            <Stat
              label="Max DD"
              value={equityPts.length ? `${(maxDD * 100).toFixed(1)}%` : '—'}
              color={maxDD >= 0.10 ? 'text-anchor-red' : maxDD >= 0.05 ? 'text-amber-400' : 'text-anchor-muted'}
            />
          </div>
        </div>

        {/* Equity curve */}
        <EquityCurve data={equityPts} height={260} />
      </GlowCard>

      {/* Bottom grid: positions + AI brief, equal weight */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">

        {/* Open positions */}
        <GlowCard padding={false} className="p-5">
          <div className="flex items-center justify-between mb-5">
            <h2 className="text-sm font-semibold text-anchor-text">Open Positions</h2>
            {positions.length > 0 && (
              <div className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-anchor-green animate-glow-pulse" />
                <span className="text-xs text-anchor-muted font-mono">{positions.length} active</span>
              </div>
            )}
          </div>
          <PositionsTable positions={positions} />
        </GlowCard>

        {/* Intelligence brief */}
        <GlowCard padding={false} className="p-5">
          <h2 className="text-sm font-semibold text-anchor-text mb-4">Intelligence Brief</h2>
          <AIBriefPanel
            content={brief?.content ?? null}
            sessionType={brief?.type ?? 'PRE'}
            timestamp={brief?.created_at}
            typewrite={false}
          />
        </GlowCard>
      </div>
    </div>
  )
}
