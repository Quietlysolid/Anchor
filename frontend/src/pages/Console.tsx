import { useMemo } from 'react'
import { EquityCurve } from '../components/charts/EquityCurve'
import { AnimatedNumber } from '../components/ui/AnimatedNumber'
import { useEquityCurve, useTradeJournal } from '../api/hooks'
import { usePositionStore, useSystemStore } from '../store'
import type { Position } from '../types'

function holdsFor(openedAt: string): string {
  const secs = Math.floor((Date.now() - new Date(openedAt).getTime()) / 1000)
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function OpenTradeCard({ pos }: { pos: Position }) {
  const isLong = pos.direction === 'LONG'
  const pl     = pos.unrealized_pl
  const winning = pl >= 0

  return (
    <div className={`rounded-2xl p-4 border ${
      winning
        ? 'bg-anchor-green/5 border-anchor-green/20'
        : 'bg-anchor-red/5  border-anchor-red/20'
    }`}>
      <div className="flex justify-between items-center gap-4">
        <div className="min-w-0">
          <p className="text-anchor-text font-semibold text-base">
            {pos.instrument.replace('_', '/')}
          </p>
          <p className="text-anchor-muted text-sm mt-0.5">
            {isLong ? 'Expecting it to rise ↑' : 'Expecting it to drop ↓'}
          </p>
        </div>
        <div className="text-right shrink-0">
          <AnimatedNumber
            value={Math.abs(pl)}
            prefix={pl >= 0 ? '+$' : '-$'}
            decimals={2}
            className={`font-mono font-semibold text-lg ${winning ? 'text-anchor-green' : 'text-anchor-red'}`}
          />
          <p className="text-anchor-muted text-xs mt-0.5">{holdsFor(pos.opened_at)}</p>
        </div>
      </div>
    </div>
  )
}

export default function Home() {
  const { data: equityData } = useEquityCurve()
  const { data: apiTrades }  = useTradeJournal()
  const { equity }           = useSystemStore()
  const positions            = usePositionStore(s => s.positions)

  const equityPts = useMemo(() => equityData ?? [], [equityData])
  const trades    = useMemo(() => apiTrades  ?? [], [apiTrades])

  const latestPt  = equityPts[equityPts.length - 1]
  const equityVal = equity > 0 ? equity : (latestPt?.account_equity ?? 0)

  // Today's P&L — equity now vs last close from a previous ET calendar day
  const todayPL = useMemo(() => {
    if (!equityVal || !equityPts.length) return null
    const etToday = new Date().toLocaleDateString('en-US', { timeZone: 'America/New_York' })
    const prevPt  = [...equityPts].reverse().find(p =>
      new Date(p.time).toLocaleDateString('en-US', { timeZone: 'America/New_York' }) !== etToday
    )
    return prevPt != null ? equityVal - prevPt.account_equity : null
  }, [equityPts, equityVal])

  // This month's stats from closed trades
  const monthStats = useMemo(() => {
    const etNow    = new Date()
    const nowMonth = etNow.toLocaleDateString('en-US', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit' })
    const monthTrades = trades.filter(t => {
      const tKey = new Date(t.closed_at).toLocaleDateString('en-US', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit' })
      return tKey === nowMonth
    })
    const wins = monthTrades.filter(t => t.net_pl > 0).length
    const pl   = monthTrades.reduce((s, t) => s + t.net_pl, 0)
    return { wins, losses: monthTrades.length - wins, pl, count: monthTrades.length }
  }, [trades])

  const dateLabel = new Date().toLocaleDateString('en-US', {
    weekday: 'long', month: 'short', day: 'numeric',
    timeZone: 'America/New_York',
  })

  const todayColor = todayPL == null ? 'text-anchor-muted'
    : todayPL >= 0 ? 'text-anchor-green'
    : 'text-anchor-red'

  return (
    <div className="min-h-screen bg-anchor-void px-4 pt-4 pb-24 md:pb-8 space-y-4">

      {/* Today's P&L — the hero */}
      <div className="text-center py-6">
        <p className="text-anchor-muted text-sm mb-4">{dateLabel}</p>
        {todayPL != null ? (
          <AnimatedNumber
            value={Math.abs(todayPL)}
            prefix={todayPL >= 0 ? '+$' : '-$'}
            decimals={2}
            className={`text-6xl sm:text-7xl font-semibold font-mono tabular-nums leading-none ${todayColor}`}
          />
        ) : (
          <span className="text-6xl font-semibold font-mono text-anchor-muted">—</span>
        )}
        <p className="text-anchor-muted/60 text-xs mt-3">Today's profit & loss</p>
      </div>

      {/* Account + This month */}
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-anchor-surface rounded-2xl p-4">
          <p className="text-anchor-muted text-xs mb-2">Your Account</p>
          <p className="text-anchor-text text-xl font-mono font-semibold">
            {equityVal > 0
              ? `$${equityVal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
              : '—'}
          </p>
        </div>
        <div className="bg-anchor-surface rounded-2xl p-4">
          <p className="text-anchor-muted text-xs mb-2">This Month</p>
          {monthStats.count > 0 ? (
            <>
              <p className={`text-xl font-mono font-semibold ${monthStats.pl >= 0 ? 'text-anchor-green' : 'text-anchor-red'}`}>
                {monthStats.pl >= 0 ? '+' : '-'}${Math.abs(monthStats.pl).toFixed(2)}
              </p>
              <p className="text-anchor-muted text-xs mt-1">
                {monthStats.wins}W · {monthStats.losses}L
              </p>
            </>
          ) : (
            <p className="text-anchor-muted text-xl font-mono">—</p>
          )}
        </div>
      </div>

      {/* Open trades */}
      {positions.length > 0 ? (
        <div className="space-y-3">
          <p className="text-anchor-muted text-xs px-1">
            {positions.length} trade{positions.length !== 1 ? 's' : ''} open right now
          </p>
          {positions.map(pos => <OpenTradeCard key={pos.id} pos={pos} />)}
        </div>
      ) : (
        <div className="bg-anchor-surface rounded-2xl p-5 text-center">
          <p className="text-anchor-muted text-sm">No trades open right now</p>
          <p className="text-anchor-muted/50 text-xs mt-1">The bot is watching the market</p>
        </div>
      )}

      {/* Account growth chart */}
      {equityPts.length > 0 && (
        <div className="bg-anchor-surface rounded-2xl p-4">
          <p className="text-anchor-muted text-xs mb-4">Account Growth</p>
          <EquityCurve data={equityPts} height={200} />
        </div>
      )}

    </div>
  )
}
