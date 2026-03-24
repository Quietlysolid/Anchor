import { useMemo } from 'react'
import { useCalendar, useIntelligenceBrief, useTodayActivity, useRolloutConfig, useLCRPairStatus, useLCRPairStatusHistory, useSystemHealth, type LCRPairStatus, type LCRStatusSnapshot } from '../api/hooks'
import { useSystemStore } from '../store'
import { RolloutCard } from '../components/panels/RolloutCard'

function timeUntil(isoStr: string): string {
  const diff = new Date(isoStr).getTime() - Date.now()
  if (diff <= 0) return 'now'
  const h = Math.floor(diff / 3_600_000)
  const m = Math.floor((diff % 3_600_000) / 60_000)
  if (h > 0) return `in ${h}h ${m}m`
  return `in ${m}m`
}

function formatReason(r: string): string {
  if (r === 'FRAGILE_ROBUSTNESS') return 'Looks weak when trading gets harder'
  if (r === 'SPREAD_BREACH') return 'Real spread is worse than expected'
  if (r === 'FRAGILE_ROBUSTNESS_AND_SPREAD_BREACH') return 'This pair looks weak, and trading it costs more than expected'
  if (r.startsWith('ZERO_WINS_AFTER_')) return `${r.replace('ZERO_WINS_AFTER_', '').replace('_LOSSES', '')} trades, no wins`
  if (r.startsWith('LOSS_CONCENTRATION_')) {
    const n = r.replace('LOSS_CONCENTRATION_', '').replace('_ZERO_WINS', '')
    return `This pair caused ${n} of all losses and has no wins`
  }
  if (r.startsWith('ZERO_ENTRIES_AFTER_')) return `No trades after ${r.replace('ZERO_ENTRIES_AFTER_', '').replace('_DAYS', '')} days`
  return r
}

function statusDot(status: string) {
  if (status === 'active')    return 'bg-anchor-green'
  if (status === 'watchlist') return 'bg-amber-400'
  return 'bg-anchor-red'
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

function FreshnessBadge({ value, staleAfterSeconds }: { value: Date | string | null | undefined; staleAfterSeconds: number }) {
  const ts = value instanceof Date ? value.getTime() : value ? new Date(value).getTime() : NaN
  const secs = Number.isNaN(ts) ? null : Math.max(0, Math.round((Date.now() - ts) / 1000))
  const tone = secs == null ? 'unknown' : secs > staleAfterSeconds ? 'stale' : 'fresh'
  const cls = tone === 'fresh'
    ? 'text-anchor-green border-anchor-green/20 bg-anchor-green/5'
    : 'text-amber-300 border-amber-400/20 bg-amber-400/10'

  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-1 text-[10px] font-mono ${cls}`}>
      updated {ageText(value)}
    </span>
  )
}

function StatusCard({
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
  freshness?: Date | string | null
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
        {freshness && <FreshnessBadge value={freshness} staleAfterSeconds={60} />}
      </div>
      <p className="mt-3 text-sm text-anchor-muted leading-relaxed">{detail}</p>
    </div>
  )
}

function LCRStatusHistoryTable({ snapshots }: { snapshots: LCRStatusSnapshot[] }) {
  if (snapshots.length === 0) return (
    <p className="text-anchor-muted/50 text-xs">No history yet. First record saves tonight.</p>
  )

  // Collect all instruments seen across snapshots
  const instruments = Array.from(
    new Set(snapshots.flatMap(s => s.pairs.map(p => p.instrument)))
  ).sort()

  // Build lookup: date → instrument → status
  const lookup: Record<string, Record<string, string>> = {}
  for (const snap of snapshots) {
    lookup[snap.date] = {}
    for (const p of snap.pairs) lookup[snap.date][p.instrument] = p.status
  }

  // Detect transitions: for each snapshot, compare to the next (older) one
  const dates = snapshots.map(s => s.date)

  return (
    <>
      <div className="space-y-2 md:hidden">
        {dates.map((date, i) => {
          const prev = i < dates.length - 1 ? lookup[dates[i + 1]] : null
          return (
            <div key={date} className="rounded-xl border border-anchor-border/30 bg-anchor-void/40 p-3">
              <p className="text-[11px] font-mono text-anchor-muted/70 mb-2">{date}</p>
              <div className="grid grid-cols-2 gap-x-3 gap-y-2">
                {instruments.map(inst => {
                  const status = lookup[date]?.[inst]
                  const prevStatus = prev?.[inst]
                  const changed = prevStatus && prevStatus !== status
                  return (
                    <div key={inst} className="flex items-center justify-between gap-2">
                      <span className="text-[11px] font-mono text-anchor-text">
                        {inst.replace('_', '/')}
                      </span>
                      <span className="flex items-center gap-1.5">
                        {changed && <span className="text-[9px] font-mono text-amber-300">chg</span>}
                        <span className={`inline-block h-2.5 w-2.5 rounded-full ${status ? statusDot(status) : 'bg-anchor-border'}`} />
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
        <p className="text-anchor-muted/40 text-[10px]">`chg` means the pair status changed from the prior snapshot.</p>
      </div>

      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr>
              <th className="text-anchor-muted/50 text-left pb-1.5 pr-3 font-normal">date</th>
              {instruments.map(inst => (
                <th key={inst} className="text-anchor-muted/50 text-center pb-1.5 px-1 font-normal">
                  {inst.replace('_', '/').replace(/USD$/, '').replace(/^USD/, '').replace(/EUR\//, 'E/').replace(/NZD\//, 'N/')}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {dates.map((date, i) => {
              const prev = i < dates.length - 1 ? lookup[dates[i + 1]] : null
              return (
                <tr key={date} className="border-t border-anchor-border/20">
                  <td className="text-anchor-muted/70 pr-3 py-1">{date.slice(5)}</td>
                  {instruments.map(inst => {
                    const status = lookup[date]?.[inst]
                    const prevStatus = prev?.[inst]
                    const changed = prevStatus && prevStatus !== status
                    return (
                      <td key={inst} className="text-center py-1 px-1">
                        <span className={`inline-block w-2 h-2 rounded-full ${status ? statusDot(status) : 'bg-anchor-border'} ${changed ? 'ring-1 ring-white/40' : ''}`} />
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
        <p className="text-anchor-muted/40 text-[10px] mt-2">Ring = status changed from the day before</p>
      </div>
    </>
  )
}

function LCRPairStatusCard({ statuses, daysLive, history }: {
  statuses: LCRPairStatus[]
  daysLive: number
  history: LCRStatusSnapshot[]
}) {
  const hasIssues = statuses.some(s => s.status !== 'active')

  return (
    <div className="rounded-2xl bg-anchor-surface p-5 space-y-3">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-anchor-muted text-xs">Pair status</p>
        <p className="text-anchor-muted/50 text-xs">Live for {daysLive} days</p>
      </div>

      {!hasIssues && (
        <p className="text-anchor-muted text-xs">All pairs are okay.</p>
      )}

      <div className="space-y-2">
        {statuses.map(s => {
          const dot =
            s.status === 'active'    ? 'bg-anchor-green' :
            s.status === 'watchlist' ? 'bg-amber-400' :
                                       'bg-anchor-red'
          const label =
            s.status === 'active'    ? 'okay' :
            s.status === 'watchlist' ? 'watch closely' :
                                       'stopped'
          const labelColor =
            s.status === 'active'    ? 'text-anchor-muted' :
            s.status === 'watchlist' ? 'text-amber-400' :
                                       'text-anchor-red'

          return (
            <div key={s.instrument}>
              <div className="flex items-center gap-2.5">
                <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
                <span className="text-anchor-text text-sm font-mono">
                  {s.instrument.replace('_', '/')}
                </span>
                <span className={`text-xs ml-auto ${labelColor}`}>{label}</span>
              </div>
              {s.reasons.length > 0 && (
                <div className="ml-4 mt-0.5 space-y-0.5">
                  {s.reasons.map(r => (
                    <p key={r} className="text-anchor-muted/70 text-xs">{formatReason(r)}</p>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* History grid */}
      <div className="border-t border-anchor-border/30 pt-3">
        <p className="text-anchor-muted/50 text-xs mb-2">History</p>
        <LCRStatusHistoryTable snapshots={history} />
      </div>
    </div>
  )
}

export default function Activity() {
  const { data: calendarData }    = useCalendar()
  const { data: brief }           = useIntelligenceBrief()
  const { data: todayData, isLoading: todayLoading, isError: todayError } = useTodayActivity()
  const { data: rolloutConfig }   = useRolloutConfig()
  const { data: pairStatuses }    = useLCRPairStatus()
  const { data: pairHistory }     = useLCRPairStatusHistory(30)
  const { data: health, isLoading: healthLoading, isError: healthError } = useSystemHealth()
  const { wsConnected, lastHeartbeat } = useSystemStore()

  const events  = useMemo(() => (calendarData ?? []).slice(0, 5), [calendarData])
  const botHealthy = health?.status === 'ok' && health.stream_connected
  const feedAge = wsConnected ? ageText(lastHeartbeat) : 'unknown'
  const feedSeconds = lastHeartbeat ? Math.max(0, Math.round((Date.now() - lastHeartbeat.getTime()) / 1000)) : null
  const enabledEngines = rolloutConfig
    ? [
        rolloutConfig.trend.enabled,
        rolloutConfig.mean_reversion.enabled,
        rolloutConfig.lcr.enabled,
        rolloutConfig.m15.enabled,
      ].filter(Boolean).length
    : null
  const statusMeta = health?.last_reconciliation
    ? `Last reconciliation ${new Date(health.last_reconciliation).toLocaleString('en-US', {
        timeZone: 'America/New_York',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        hour12: true,
      })} ET`
    : null

  const signalsToday = todayData?.signals_today ?? null
  const tradesToday  = todayData?.trades_today  ?? null

  return (
    <div className="min-h-screen bg-anchor-void px-4 pt-4 pb-24 md:pb-8 space-y-4">

      {/* Bot status */}
      <div className="space-y-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-[11px] uppercase tracking-[0.18em] text-anchor-muted font-mono">Intelligence</p>
            <p className="text-anchor-text text-xl font-semibold mt-1">Context And Monitoring</p>
          </div>
          <div className="self-start sm:self-auto">
            <FreshnessBadge value={health?.timestamp ?? null} staleAfterSeconds={20} />
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          <StatusCard
            title="Engine"
            value={healthLoading ? 'Checking' : healthError || !health ? 'Unknown' : botHealthy ? 'Healthy' : 'Degraded'}
            detail={healthError || !health ? 'The dashboard could not confirm backend health.' : `Stream ${health.stream_connected ? 'connected' : 'disconnected'} · reconciliation ${health.last_reconciliation ? ageText(health.last_reconciliation) : 'unknown'}.`}
            tone={healthLoading ? 'amber' : botHealthy ? 'green' : 'red'}
            freshness={health?.timestamp ?? null}
          />
          <StatusCard
            title="Live Feed"
            value={!wsConnected ? 'Disconnected' : feedSeconds != null && feedSeconds > 30 ? 'Delayed' : 'Live'}
            detail={!wsConnected ? 'Browser websocket transport is disconnected.' : `Last heartbeat ${feedAge}.`}
            tone={!wsConnected ? 'red' : feedSeconds != null && feedSeconds > 30 ? 'amber' : 'green'}
            freshness={lastHeartbeat}
          />
          <StatusCard
            title="Trading State"
            value={enabledEngines == null ? 'Unverified' : enabledEngines === 0 ? 'Paused' : 'Enabled'}
            detail={enabledEngines == null ? 'The API does not expose a definitive halt flag.' : enabledEngines === 0 ? 'No engines are enabled in rollout config.' : `${enabledEngines} engine${enabledEngines === 1 ? '' : 's'} enabled in rollout config.`}
            tone={enabledEngines == null || enabledEngines === 0 ? 'amber' : 'green'}
            freshness={health?.last_reconciliation ?? null}
          />
        </div>
        {statusMeta && (
          <p className="text-anchor-muted/60 text-xs px-1">{statusMeta}</p>
        )}
      </div>

      {/* Strategy rollout */}
      <RolloutCard config={rolloutConfig} />

      {/* LCR pair status */}
      {pairStatuses && (
        <div className="space-y-2">
          <div className="flex flex-col gap-2 px-1 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-[11px] uppercase tracking-[0.18em] text-anchor-muted font-mono">Live controls</p>
            <div className="self-start sm:self-auto">
              <FreshnessBadge value={pairStatuses.evaluated_at} staleAfterSeconds={300} />
            </div>
          </div>
          <LCRPairStatusCard
            statuses={pairStatuses.pairs}
            daysLive={pairStatuses.days_since_live}
            history={pairHistory?.snapshots ?? []}
          />
        </div>
      )}

      {/* Today's snapshot */}
      <div className="bg-anchor-surface rounded-2xl p-5">
        <p className="text-anchor-muted text-xs mb-4">Today's Activity</p>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-anchor-text text-3xl font-semibold font-mono">
              {todayLoading ? '…' : signalsToday ?? '—'}
            </p>
            <p className="text-anchor-muted text-xs mt-1">setups checked</p>
          </div>
          <div>
            <p className="text-anchor-text text-3xl font-semibold font-mono">
              {todayLoading ? '…' : tradesToday ?? '—'}
            </p>
            <p className="text-anchor-muted text-xs mt-1">trades closed</p>
          </div>
        </div>
        {todayError && (
          <p className="text-anchor-red text-xs mt-3">Today&apos;s activity is unavailable.</p>
        )}
      </div>

      {/* Latest AI brief — plain text summary */}
      {brief?.content && (
        <div className="bg-anchor-surface rounded-2xl p-5 border border-anchor-border">
          <div className="flex flex-col gap-3 mb-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p className="text-anchor-muted text-xs">Latest Summary</p>
              <p className="text-[10px] uppercase tracking-[0.18em] text-amber-300 font-mono mt-1">Reference</p>
            </div>
            <div className="self-start sm:self-auto">
              <FreshnessBadge value={brief.created_at} staleAfterSeconds={300} />
            </div>
          </div>
          <p className="text-anchor-text text-sm leading-relaxed whitespace-pre-line">{brief.content}</p>
        </div>
      )}

      {/* Upcoming news */}
      {events.length > 0 && (
        <div className="bg-anchor-surface rounded-2xl p-5 border border-anchor-border">
          <div className="flex flex-col gap-3 mb-4 sm:flex-row sm:items-start sm:justify-between">
            <p className="text-anchor-muted text-xs">Upcoming News</p>
            <div className="self-start sm:self-auto">
              <FreshnessBadge value={health?.timestamp ?? null} staleAfterSeconds={300} />
            </div>
          </div>
          <div className="space-y-3">
            {events.map((ev, i) => (
              <div key={i} className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-anchor-text text-sm font-medium truncate">{ev.event_name}</p>
                  <p className="text-anchor-muted text-xs mt-0.5">
                    {new Date(ev.event_time).toLocaleString('en-US', {
                      timeZone: 'America/New_York',
                      weekday: 'short', month: 'short', day: 'numeric',
                      hour: 'numeric', minute: '2-digit', hour12: true,
                    })}
                  </p>
                </div>
                <p className="text-anchor-muted text-xs shrink-0 mt-0.5">
                  {timeUntil(ev.event_time)}
                </p>
              </div>
            ))}
          </div>
          <p className="text-anchor-muted/50 text-xs mt-4">
            The bot pauses for big news events.
          </p>
        </div>
      )}

    </div>
  )
}
