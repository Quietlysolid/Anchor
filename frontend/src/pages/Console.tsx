import type { ReactNode } from 'react'
import { useMemo } from 'react'
import { EquityCurve } from '../components/charts/EquityCurve'
import { AnimatedNumber } from '../components/ui/AnimatedNumber'
import { useEdgeConfidence, useEquityCurve, useLatestPerfCheck, useRolloutConfig, useSystemEvents, useTradeJournal, useSystemHealth } from '../api/hooks'
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
  const pl = pos.unrealized_pl
  const winning = pl != null && pl >= 0

  return (
    <div className={`rounded-2xl p-4 border ${
      pl == null
        ? 'bg-anchor-surface border-anchor-border'
        : winning
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
          {pl == null ? (
            <span className="font-mono font-semibold text-lg text-anchor-muted">—</span>
          ) : (
            <AnimatedNumber
              value={Math.abs(pl)}
              prefix={pl >= 0 ? '+$' : '-$'}
              decimals={2}
              className={`font-mono font-semibold text-lg ${winning ? 'text-anchor-green' : 'text-anchor-red'}`}
            />
          )}
          <p className="text-anchor-muted text-xs mt-0.5">{holdsFor(pos.opened_at)}</p>
        </div>
      </div>
    </div>
  )
}

function ageText(value: Date | string | null | undefined): string {
  if (!value) return 'unknown'
  const ts = value instanceof Date ? value.getTime() : new Date(value).getTime()
  if (Number.isNaN(ts)) return 'unknown'
  const secs = Math.max(0, Math.round((Date.now() - ts) / 1000))
  if (secs < 5) return 'just now'
  if (secs < 60) return `${secs}s ago`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

function freshnessTone(value: Date | string | null | undefined, staleAfterSeconds: number) {
  if (!value) return 'unknown'
  const ts = value instanceof Date ? value.getTime() : new Date(value).getTime()
  if (Number.isNaN(ts)) return 'unknown'
  const secs = Math.max(0, Math.round((Date.now() - ts) / 1000))
  if (secs <= staleAfterSeconds) return 'fresh'
  return 'stale'
}

function FreshnessBadge({ value, staleAfterSeconds }: { value: Date | string | null | undefined; staleAfterSeconds: number }) {
  const tone = freshnessTone(value, staleAfterSeconds)
  const cls = tone === 'fresh'
    ? 'text-anchor-green border-anchor-green/20 bg-anchor-green/5'
    : tone === 'stale'
      ? 'text-amber-300 border-amber-400/20 bg-amber-400/10'
      : 'text-amber-300 border-amber-400/20 bg-amber-400/10'

  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-1 text-[10px] font-mono ${cls}`}>
      updated {ageText(value)}
    </span>
  )
}

function StatusRailCard({
  title,
  value,
  detail,
  tone,
  freshness,
}: {
  title: string
  value: string
  detail: string
  tone: 'green' | 'amber' | 'red'
  freshness?: ReactNode
}) {
  const dot = tone === 'green' ? 'bg-anchor-green' : tone === 'amber' ? 'bg-amber-400' : 'bg-anchor-red'
  const text = tone === 'green' ? 'text-anchor-green' : tone === 'amber' ? 'text-amber-300' : 'text-anchor-red'

  return (
    <div className="rounded-2xl border border-anchor-border bg-anchor-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] uppercase tracking-[0.18em] text-anchor-muted font-mono">{title}</p>
          <div className="mt-2 flex items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${dot}`} />
            <p className={`text-base font-semibold ${text}`}>{value}</p>
          </div>
        </div>
        {freshness}
      </div>
      <p className="mt-3 text-sm text-anchor-muted leading-relaxed">{detail}</p>
    </div>
  )
}

export default function Home() {
  const { data: equityData } = useEquityCurve()
  const { data: apiTrades }  = useTradeJournal()
  const { data: health }     = useSystemHealth()
  const { data: rolloutConfig } = useRolloutConfig()
  const { data: perfCheck } = useLatestPerfCheck()
  const { data: edgeConfidence } = useEdgeConfidence()
  const { data: systemEvents } = useSystemEvents('WARNING,ERROR', 20)
  const { equity, lastHeartbeat, accountUpdatedAt } = useSystemStore()
  const positions = usePositionStore(s => s.positions)
  const positionsUpdatedAt = usePositionStore(s => s.positionsUpdatedAt)

  const equityPts = useMemo(() => equityData ?? [], [equityData])
  const trades    = useMemo(() => apiTrades  ?? [], [apiTrades])

  const latestPt  = equityPts[equityPts.length - 1]
  const equityVal = equity > 0 ? equity : (health?.account_equity ?? latestPt?.account_equity ?? 0)
  const todayPL = health?.today_pl ?? null
  const latestIssue = useMemo(() => {
    const candidate = (systemEvents ?? []).find(e =>
      e.component === 'reconcile_positions' ||
      e.component === 'execution' ||
      e.component === 'monitor_live_performance' ||
      e.component === 'edge_confidence'
    )
    return candidate ?? systemEvents?.[0] ?? null
  }, [systemEvents])

  const engineTone = !health ? 'amber' : health.status === 'ok' ? 'green' : 'red'
  const engineValue = !health ? 'Unknown' : health.status === 'ok' ? 'Healthy' : 'Degraded'
  const engineDetail = !health
    ? 'Backend health is not confirmed.'
    : `DB ${health.db}. Reconciliation ${health.last_reconciliation ? ageText(health.last_reconciliation) : 'unknown'}.`

  const feedAge = lastHeartbeat ? (Date.now() - lastHeartbeat.getTime()) / 1000 : null
  const feedTone = !lastHeartbeat || !health?.stream_connected || !health ? 'red' : feedAge != null && feedAge > 30 ? 'amber' : 'green'
  const feedValue = !lastHeartbeat || !health?.stream_connected || !health
    ? 'Disconnected'
    : feedAge != null && feedAge > 30
      ? 'Delayed'
      : 'Live'
  const feedDetail = !lastHeartbeat || !health?.stream_connected || !health
    ? 'The dashboard is not receiving live broker updates.'
    : `Last heartbeat ${ageText(lastHeartbeat)}.`

  const enabledEngines = rolloutConfig
    ? [
        rolloutConfig.trend.enabled,
        rolloutConfig.mean_reversion.enabled,
        rolloutConfig.lcr.enabled,
        rolloutConfig.m15.enabled,
      ].filter(Boolean).length
    : null
  const tradingTone = enabledEngines == null
    ? 'amber'
    : enabledEngines === 0
      ? 'amber'
      : health?.status === 'ok' && health.stream_connected
        ? 'green'
        : 'amber'
  const tradingValue = enabledEngines == null
    ? 'Unverified'
    : enabledEngines === 0
      ? 'Paused'
      : 'Enabled'
  const tradingDetail = enabledEngines == null
    ? 'The API does not currently expose a hard halt flag. State cannot be fully verified.'
    : enabledEngines === 0
      ? 'All engines are disabled in the live rollout config.'
      : ` ${enabledEngines} engine${enabledEngines === 1 ? '' : 's'} enabled. Halt flags are not exposed by the API.`

  const riskBanner = perfCheck?.severity === 'WARNING'
    ? perfCheck.message
    : edgeConfidence?.confidence === 'LOW' || edgeConfidence?.confidence === 'REDUCED'
      ? edgeConfidence.message ?? 'Edge confidence is reduced.'
      : null

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
      <div className="space-y-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-[11px] uppercase tracking-[0.18em] text-anchor-muted font-mono">Operator Console</p>
            <p className="text-anchor-text text-xl font-semibold mt-1">Live Incident View</p>
          </div>
          <div className="self-start sm:self-auto">
            <FreshnessBadge value={health?.timestamp ?? null} staleAfterSeconds={20} />
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          <StatusRailCard
            title="Engine"
            value={engineValue}
            detail={engineDetail}
            tone={engineTone}
            freshness={<FreshnessBadge value={health?.timestamp ?? null} staleAfterSeconds={20} />}
          />
          <StatusRailCard
            title="Live Feed"
            value={feedValue}
            detail={feedDetail}
            tone={feedTone}
            freshness={<FreshnessBadge value={lastHeartbeat} staleAfterSeconds={30} />}
          />
          <StatusRailCard
            title="Trading State"
            value={tradingValue}
            detail={tradingDetail.trim()}
            tone={tradingTone}
            freshness={<FreshnessBadge value={health?.last_reconciliation ?? health?.timestamp ?? null} staleAfterSeconds={60} />}
          />
        </div>
      </div>

      {riskBanner && (
        <div className="rounded-2xl border border-amber-400/20 bg-amber-400/10 p-4">
          <p className="text-[11px] uppercase tracking-[0.18em] text-amber-300 font-mono">Attention</p>
          <p className="mt-2 text-sm text-amber-100 leading-relaxed">{riskBanner}</p>
        </div>
      )}

      {/* Today's P&L — the hero */}
      <div className="rounded-3xl border border-anchor-border bg-[radial-gradient(circle_at_top,#1f3b28_0%,#1c1c1e_36%,#111214_100%)] px-5 py-6 shadow-glow-green">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-anchor-muted text-sm">{dateLabel}</p>
            <p className="text-[11px] uppercase tracking-[0.18em] text-anchor-muted font-mono mt-3">Today's P&amp;L</p>
          </div>
          <div className="self-start sm:self-auto">
            <FreshnessBadge value={health?.timestamp ?? accountUpdatedAt ?? null} staleAfterSeconds={20} />
          </div>
        </div>
        <div className="mt-5">
          {todayPL != null ? (
            <AnimatedNumber
              value={Math.abs(todayPL)}
              prefix={todayPL >= 0 ? '+$' : '-$'}
              decimals={2}
              className={`text-6xl sm:text-7xl font-semibold font-mono tabular-nums leading-none ${todayColor}`}
            />
          ) : (
            <span className="text-6xl font-semibold font-mono text-amber-300">—</span>
          )}
          <p className="text-anchor-muted/70 text-xs mt-3">Server-defined daily profit &amp; loss</p>
        </div>
      </div>

      {/* Account + This month */}
      <div className="grid gap-3 md:grid-cols-3">
        <div className="bg-anchor-surface rounded-2xl p-4 border border-anchor-border">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <p className="text-anchor-muted text-xs mb-2">Account equity</p>
            <div className="self-start sm:self-auto">
              <FreshnessBadge value={accountUpdatedAt ?? health?.timestamp ?? null} staleAfterSeconds={20} />
            </div>
          </div>
          <p className="text-anchor-text text-xl font-mono font-semibold">
            {equityVal > 0
              ? `$${equityVal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
              : '—'}
          </p>
        </div>
        <div className="bg-anchor-surface rounded-2xl p-4 border border-anchor-border">
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
            <p className="text-amber-300 text-xl font-mono">—</p>
          )}
        </div>
        <div className="bg-anchor-surface rounded-2xl p-4 border border-anchor-border">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <p className="text-anchor-muted text-xs mb-2">Open positions</p>
            <div className="self-start sm:self-auto">
              <FreshnessBadge value={positionsUpdatedAt} staleAfterSeconds={15} />
            </div>
          </div>
          <p className="text-anchor-text text-xl font-mono font-semibold">{positions.length}</p>
          <p className="text-anchor-muted text-xs mt-1">
            {positions.length > 0 ? 'Live exposure on the book' : 'No open exposure confirmed'}
          </p>
        </div>
      </div>

      {latestIssue && (
        <div className="rounded-2xl border border-anchor-red/20 bg-anchor-red/10 p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p className="text-[11px] uppercase tracking-[0.18em] text-anchor-red font-mono">Latest Warning</p>
              <p className="mt-2 text-sm text-white leading-relaxed">{latestIssue.message}</p>
            </div>
            <div className="self-start sm:self-auto">
              <FreshnessBadge value={latestIssue.event_at} staleAfterSeconds={300} />
            </div>
          </div>
          <p className="mt-3 text-xs text-white/60 font-mono">
            {latestIssue.component ?? 'system'} · {latestIssue.severity} · {latestIssue.event_type}
          </p>
        </div>
      )}

      {/* Open trades */}
      {positions.length > 0 ? (
        <div className="space-y-3">
          <div className="flex flex-col gap-2 px-1 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-anchor-muted text-xs">
              Open exposure
            </p>
            <div className="self-start sm:self-auto">
              <FreshnessBadge value={positionsUpdatedAt} staleAfterSeconds={15} />
            </div>
          </div>
          <p className="text-anchor-muted/60 text-xs px-1">
            {positions.length} trade{positions.length !== 1 ? 's' : ''} open right now
          </p>
          {positions.map(pos => <OpenTradeCard key={pos.id} pos={pos} />)}
        </div>
      ) : (
        <div className="bg-anchor-surface rounded-2xl p-5 text-center border border-anchor-border">
          <div className="flex justify-center mb-3">
            <FreshnessBadge value={positionsUpdatedAt} staleAfterSeconds={15} />
          </div>
          <p className="text-anchor-text text-sm">No trades open right now</p>
          <p className="text-anchor-muted/50 text-xs mt-1">No live exposure is currently confirmed</p>
        </div>
      )}

      {/* Account growth chart */}
      {equityPts.length > 0 && (
        <div className="bg-anchor-surface rounded-2xl p-4 border border-anchor-border">
          <div className="flex flex-col gap-3 mb-4 sm:flex-row sm:items-start sm:justify-between">
            <p className="text-anchor-muted text-xs">Account Growth</p>
            <div className="self-start sm:self-auto">
              <FreshnessBadge value={latestPt?.time ?? null} staleAfterSeconds={120} />
            </div>
          </div>
          <EquityCurve data={equityPts} height={200} />
        </div>
      )}

    </div>
  )
}
