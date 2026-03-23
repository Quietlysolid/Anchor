import { useMemo } from 'react'
import { useCalendar, useIntelligenceBrief, useTodayActivity, useRolloutConfig, useLCRPairStatus, useLCRPairStatusHistory, type LCRPairStatus, type LCRStatusSnapshot } from '../api/hooks'
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

function sessionStatus(): { label: string; sub: string; active: boolean } {
  const now = new Date()
  const utcHour = now.getUTCHours()
  const utcDay  = now.getUTCDay() // 0=Sun, 6=Sat

  const weekend = utcDay === 0 || utcDay === 6
  const fridayAfterClose = utcDay === 5 && utcHour >= 20

  if (weekend || fridayAfterClose) {
    // Next London open: Monday 07:00 UTC
    const daysUntilMon = ((8 - utcDay) % 7) || 7
    const nextMon = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + daysUntilMon, 7, 0, 0))
    const minsUntil = Math.round((nextMon.getTime() - now.getTime()) / 60_000)
    const h = Math.floor(minsUntil / 60)
    const m = minsUntil % 60
    return { label: 'Markets closed', sub: `London session opens Monday in ${h}h ${m}m`, active: false }
  }

  if (utcHour >= 7 && utcHour < 12) {
    return { label: 'London session is active', sub: 'The bot is watching for trades', active: true }
  }
  if (utcHour >= 17 && utcHour < 20) {
    return { label: 'Evening trades are active', sub: 'The bot is looking for evening trades', active: true }
  }
  if (utcHour < 7) {
    const minsUntil = (7 - utcHour) * 60 - now.getUTCMinutes()
    const h = Math.floor(minsUntil / 60)
    const m = minsUntil % 60
    return { label: 'Market watching begins soon', sub: `London session starts in ${h}h ${m}m`, active: false }
  }
  if (utcHour >= 12 && utcHour < 17) {
    const minsUntil = (17 - utcHour) * 60 - now.getUTCMinutes()
    const h = Math.floor(minsUntil / 60)
    const m = minsUntil % 60
    return { label: 'Quiet period', sub: `Evening trades open in ${h}h ${m}m`, active: false }
  }
  // 20:00–24:00 UTC on a weekday — next session is London tomorrow
  const minsUntil = (24 - utcHour) * 60 - now.getUTCMinutes() + 7 * 60
  const h = Math.floor(minsUntil / 60)
  const m = minsUntil % 60
  return { label: 'Markets closed', sub: `London session opens tomorrow in ${h}h ${m}m`, active: false }
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
    <div className="overflow-x-auto">
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
      <div className="flex items-center justify-between">
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
  const { data: todayData }       = useTodayActivity()
  const { data: rolloutConfig }   = useRolloutConfig()
  const { data: pairStatuses }    = useLCRPairStatus()
  const { data: pairHistory }     = useLCRPairStatusHistory(30)
  const { wsConnected }           = useSystemStore()

  const events  = useMemo(() => (calendarData ?? []).slice(0, 5), [calendarData])
  const session = sessionStatus()

  const signalsToday = todayData?.signals_today ?? 0
  const tradesToday  = todayData?.trades_today  ?? 0

  return (
    <div className="min-h-screen bg-anchor-void px-4 pt-4 pb-24 md:pb-8 space-y-4">

      {/* Bot status */}
      <div className="bg-anchor-surface rounded-2xl p-5">
        <div className="flex items-center gap-3 mb-4">
          <div className={`w-2.5 h-2.5 rounded-full ${wsConnected ? 'bg-anchor-green animate-pulse' : 'bg-anchor-red'}`} />
          <p className="text-anchor-text font-semibold">
            {wsConnected ? 'Bot is running' : 'Bot is offline'}
          </p>
        </div>
        <p className="text-anchor-text text-sm font-medium">{session.label}</p>
        <p className="text-anchor-muted text-sm mt-0.5">{session.sub}</p>
      </div>

      {/* Strategy rollout */}
      <RolloutCard config={rolloutConfig} />

      {/* LCR pair status */}
      {pairStatuses && (
        <LCRPairStatusCard
          statuses={pairStatuses.pairs}
          daysLive={pairStatuses.days_since_live}
          history={pairHistory?.snapshots ?? []}
        />
      )}

      {/* Is it working — precommitted checks (set 2026-03-23, before live trades) */}
      <div className="rounded-2xl bg-anchor-surface p-5 space-y-4">
        <div>
          <p className="text-anchor-muted text-xs mb-1">Is it working?</p>
          <p className="text-anchor-text text-sm font-medium">This setup is not proven yet.</p>
          <p className="text-anchor-muted text-xs mt-1">
            We need at least 50 live trades before we can judge. Until then, the honest answer is: we do not know.
          </p>
        </div>

        <div className="border-t border-anchor-border/40 pt-4 space-y-3">
          <p className="text-anchor-muted text-xs">Stop using it after 50 live trades if any of these are true</p>
          {[
            'It is losing more money than it makes',
            'It wins less than 40% of trades (test results were 48–51%)',
            'One pair is responsible for most of the losses and has no wins at all',
            'Trades are closing much faster or much slower than expected',
          ].map(criterion => (
            <div key={criterion} className="flex items-start gap-2.5">
              <span className="text-anchor-muted text-xs mt-0.5 shrink-0">—</span>
              <p className="text-anchor-muted text-xs">{criterion}</p>
            </div>
          ))}
        </div>

        <div className="border-t border-anchor-border/40 pt-4 space-y-3">
          <p className="text-anchor-muted text-xs">Early warning signs to watch</p>
          {[
            'Stops get hit too often — more than half the time (currently 44% on 9 trades)',
            'The real cost to trade is more than twice what we expected on any pair',
            'AUD/USD or USD/CAD still has no trades after 60 days',
            'Any pair with 15 or more trades and no wins',
          ].map(criterion => (
            <div key={criterion} className="flex items-start gap-2.5">
              <span className="text-anchor-muted text-xs mt-0.5 shrink-0">—</span>
              <p className="text-anchor-muted text-xs">{criterion}</p>
            </div>
          ))}
        </div>

        <div className="border-t border-anchor-border/40 pt-4 space-y-3">
          <p className="text-anchor-muted text-xs">
            Stress test results — locked 2026-03-23, before any live trades
          </p>
          <p className="text-anchor-muted/70 text-xs">
            How each pair holds up when trading costs are doubled and orders slip. Based on 8 years of data.
          </p>
          <div className="font-mono text-xs space-y-1.5">
            {[
              { pair: 'EUR/USD', base: '1.384', stress2x: '1.360', combined: '1.214', fragile: false },
              { pair: 'NZD/USD', base: '1.341', stress2x: '1.263', combined: '1.142', fragile: false },
              { pair: 'AUD/USD', base: '1.242', stress2x: '1.129', combined: '0.992', fragile: true  },
              { pair: 'EUR/JPY', base: '1.162', stress2x: '1.038', combined: '0.966', fragile: true  },
              { pair: 'USD/CAD', base: '1.143', stress2x: '1.034', combined: '0.933', fragile: true  },
            ].map(r => (
              <div key={r.pair} className="grid grid-cols-[4rem_1fr_1fr_1fr_auto] gap-2 items-center">
                <span className="text-anchor-text">{r.pair}</span>
                <span className="text-anchor-muted text-right">{r.base}</span>
                <span className="text-anchor-muted text-right">{r.stress2x}</span>
                <span className={`text-right ${r.fragile ? 'text-amber-400' : 'text-anchor-muted'}`}>{r.combined}</span>
                <span className={`text-[10px] ${r.fragile ? 'text-amber-400' : 'text-anchor-green'}`}>
                  {r.fragile ? 'weak' : 'strong'}
                </span>
              </div>
            ))}
            <div className="grid grid-cols-[4rem_1fr_1fr_1fr_auto] gap-2 items-center pt-1 border-t border-anchor-border/30">
              <span className="text-anchor-muted/50" />
              <span className="text-anchor-muted/50 text-right text-[10px]">normal</span>
              <span className="text-anchor-muted/50 text-right text-[10px]">double costs</span>
              <span className="text-anchor-muted/50 text-right text-[10px]">worst case</span>
              <span />
            </div>
          </div>
        </div>

        <div className="border-t border-anchor-border/40 pt-4 space-y-2">
          <p className="text-anchor-muted text-xs">Strategies we have ruled out</p>
          <p className="text-anchor-muted/60 text-xs">Mean reversion — did not work in testing. Turned off.</p>
          <p className="text-anchor-muted/60 text-xs">London trend — never placed a trade live. Turned off.</p>
        </div>
      </div>

      {/* Today's snapshot */}
      <div className="bg-anchor-surface rounded-2xl p-5">
        <p className="text-anchor-muted text-xs mb-4">Today's Activity</p>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-anchor-text text-3xl font-semibold font-mono">{signalsToday}</p>
            <p className="text-anchor-muted text-xs mt-1">setups checked</p>
          </div>
          <div>
            <p className="text-anchor-text text-3xl font-semibold font-mono">{tradesToday}</p>
            <p className="text-anchor-muted text-xs mt-1">trades placed</p>
          </div>
        </div>
      </div>

      {/* Latest AI brief — plain text summary */}
      {brief?.content && (
        <div className="bg-anchor-surface rounded-2xl p-5">
          <p className="text-anchor-muted text-xs mb-3">Latest Summary</p>
          <p className="text-anchor-text text-sm leading-relaxed whitespace-pre-line">{brief.content}</p>
        </div>
      )}

      {/* Upcoming news */}
      {events.length > 0 && (
        <div className="bg-anchor-surface rounded-2xl p-5">
          <p className="text-anchor-muted text-xs mb-4">Upcoming News</p>
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
