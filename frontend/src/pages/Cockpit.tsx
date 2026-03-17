import { useMemo } from 'react'
import { GlowCard } from '../components/ui/GlowCard'
import { AnimatedNumber } from '../components/ui/AnimatedNumber'
import { EquityCurve } from '../components/charts/EquityCurve'
import { AIBriefPanel } from '../components/panels/AIBriefPanel'
import { EconomicCalendar } from '../components/panels/EconomicCalendar'
import { PositionsTable } from '../components/panels/PositionsTable'
import { RegimeBadge } from '../components/ui/RegimeBadge'
import { useEquityCurve, useCalendar, useIntelligenceBrief } from '../api/hooks'
import { usePositionStore, useSystemStore } from '../store'
import { MOCK_EQUITY, MOCK_POSITIONS, MOCK_CALENDAR, MOCK_BRIEF_CONTENT } from '../mock/data'

export default function Dashboard() {
  const { data: equityData }   = useEquityCurve()
  const { data: calendarData } = useCalendar()
  const { data: brief }        = useIntelligenceBrief()
  const { equity, currentRegime } = useSystemStore()
  const positions = usePositionStore(s => s.positions)

  const equityPts = equityData?.length  ? equityData  : MOCK_EQUITY
  const calEvents = calendarData?.length ? calendarData : MOCK_CALENDAR
  const livePos   = positions.length     ? positions   : MOCK_POSITIONS

  const latestEquity = equityPts[equityPts.length - 1]
  const firstEquity  = equityPts[0]
  const equityVal    = equity || latestEquity?.account_equity || 10000

  const dailyPts = equityPts.slice(-2)
  const dailyPL  = dailyPts.length >= 2
    ? dailyPts[dailyPts.length - 1].account_equity - dailyPts[0].account_equity
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

  const totalGainPct = firstEquity
    ? ((equityVal - firstEquity.account_equity) / firstEquity.account_equity) * 100
    : 0

  const dominantRegime = Object.entries(currentRegime)[0]?.[1]?.state ?? 'TRENDING'
  const plPositive     = dailyPL >= 0

  return (
    <div className="min-h-screen bg-anchor-void p-5 space-y-4">

      {/* ── Equity card ───────────────────────────────────────── */}
      <GlowCard padding={false} className="p-5 pb-3">
        {/* Stats row */}
        <div className="flex items-start justify-between gap-6 mb-5">

          {/* Primary: portfolio value */}
          <div>
            <p className="text-xs text-anchor-muted mb-1.5 tracking-wide">Portfolio Value</p>
            <AnimatedNumber
              value={equityVal}
              prefix="$"
              decimals={2}
              className="text-4xl font-semibold text-anchor-text tabular-nums"
            />
            <span className={`inline-block mt-1 text-sm font-mono ${totalGainPct >= 0 ? 'text-anchor-green' : 'text-anchor-red'}`}>
              {totalGainPct >= 0 ? '+' : ''}{totalGainPct.toFixed(2)}% all time
            </span>
          </div>

          {/* Secondary stats */}
          <div className="flex items-start gap-8 pt-1">
            <div>
              <p className="text-xs text-anchor-muted mb-1.5 tracking-wide">Today</p>
              <AnimatedNumber
                value={Math.abs(dailyPL)}
                prefix={plPositive ? '+$' : '−$'}
                decimals={2}
                className={`text-xl font-mono font-semibold ${plPositive ? 'text-anchor-green' : 'text-anchor-red'}`}
              />
            </div>
            <div>
              <p className="text-xs text-anchor-muted mb-1.5 tracking-wide">Max Drawdown</p>
              <span className={`text-xl font-mono font-semibold ${maxDD >= 0.10 ? 'text-anchor-red' : maxDD >= 0.05 ? 'text-amber-400' : 'text-anchor-text'}`}>
                {(maxDD * 100).toFixed(1)}%
              </span>
            </div>
            <div>
              <p className="text-xs text-anchor-muted mb-1.5 tracking-wide">Positions</p>
              <span className="text-xl font-mono font-semibold text-anchor-text">{livePos.length}</span>
            </div>
            <div className="flex flex-col justify-end pb-0.5">
              <RegimeBadge regime={dominantRegime} />
            </div>
          </div>
        </div>

        {/* Chart */}
        <EquityCurve data={equityPts} height={200} />
      </GlowCard>

      {/* ── Bottom grid ───────────────────────────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-[1fr_320px] gap-4">

        {/* Positions */}
        <GlowCard padding={false} className="p-5">
          <div className="flex items-center justify-between mb-5">
            <h2 className="text-sm font-semibold text-anchor-text">Open Positions</h2>
            <span className="text-xs text-anchor-muted font-mono">{livePos.length} active</span>
          </div>
          <PositionsTable positions={livePos} />
        </GlowCard>

        {/* Right column */}
        <div className="space-y-4">
          <GlowCard padding={false} className="p-5">
            <AIBriefPanel
              content={brief?.content ?? MOCK_BRIEF_CONTENT}
              sessionType={brief?.type ?? 'PRE'}
              timestamp={brief?.created_at}
              typewrite={false}
            />
          </GlowCard>

          <GlowCard padding={false} className="p-5">
            <h2 className="text-sm font-semibold text-anchor-text mb-4">Upcoming Events</h2>
            <EconomicCalendar events={calEvents} />
          </GlowCard>
        </div>
      </div>
    </div>
  )
}
