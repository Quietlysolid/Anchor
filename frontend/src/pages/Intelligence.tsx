import { useCalendar, useIntelligenceBrief, useTodayActivity, useRolloutConfig, useLCRPairStatus, useLCRPairStatusHistory, useSystemHealth } from '../api/hooks'
import { useSystemStore } from '../store'

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

function formatReason(reason: string): string {
  const raw = reason.toUpperCase()
  if (raw === 'FRAGILE_ROBUSTNESS') return 'Weak in live trading'
  if (raw === 'SPREAD_BREACH') return 'Spread too wide'
  if (raw === 'FRAGILE_ROBUSTNESS_AND_SPREAD_BREACH') return 'Weak + spread too wide'
  if (raw.startsWith('ZERO_WINS_AFTER_')) return 'Lost too many without a win'
  if (raw.startsWith('LOSS_CONCENTRATION_')) return 'Too many losses from this pair'
  if (raw.startsWith('ZERO_ENTRIES_AFTER_')) return 'No live trade for too long'
  return raw.replace(/_/g, ' ').toLowerCase()
}

function modeLabel(key: 'trend' | 'mean_reversion' | 'lcr' | 'm15') {
  if (key === 'mean_reversion') return 'Mean reversion'
  if (key === 'lcr') return 'London Close'
  if (key === 'm15') return 'M15'
  return 'Trend'
}

export default function Intelligence() {
  const { data: calendarData } = useCalendar()
  const { data: brief } = useIntelligenceBrief()
  const { data: todayData } = useTodayActivity()
  const { data: rolloutConfig } = useRolloutConfig()
  const { data: pairStatuses } = useLCRPairStatus()
  const { data: pairHistory } = useLCRPairStatusHistory(30)
  const { data: health } = useSystemHealth()
  const { wsConnected, lastHeartbeat } = useSystemStore()

  const events = (calendarData ?? []).slice(0, 6)
  const liveModes = rolloutConfig
    ? (['trend', 'mean_reversion', 'lcr', 'm15'] as const).filter(key => rolloutConfig[key].enabled && !rolloutConfig[key].paper_only)
    : []
  const paperModes = rolloutConfig
    ? (['trend', 'mean_reversion', 'lcr', 'm15'] as const).filter(key => rolloutConfig[key].enabled && rolloutConfig[key].paper_only)
    : []
  const latestPairSnapshot = pairHistory?.snapshots?.[0] ?? null
  const pairUniverse = rolloutConfig?.instruments ?? []
  const accountMode = health?.account_mode ?? rolloutConfig?.account_mode ?? 'paper'
  const feedAgeSeconds = lastHeartbeat ? Math.max(0, Math.round((Date.now() - lastHeartbeat.getTime()) / 1000)) : null
  const feedOk = health?.stream_connected && (feedAgeSeconds == null || feedAgeSeconds <= 20)

  return (
    <div className="min-h-screen bg-anchor-void pb-20 md:pb-0">

      {/* ── Header + AI brief as hero ─────────────────────── */}
      <div className="px-8 pt-10 pb-9 md:px-12 md:pt-12 border-b border-anchor-rule">
        <p className="font-mono text-[9px] tracking-[0.32em] uppercase text-anchor-muted/45 mb-6">Intel</p>

        {brief?.content ? (
          <p className="text-xl md:text-2xl leading-[1.65] text-anchor-text max-w-3xl font-light">
            {brief.content}
          </p>
        ) : (
          <>
            <h1 className="font-serif text-[3.2rem] md:text-[4.4rem] leading-[0.92] tracking-[-0.02em] text-anchor-text">
              What I'm thinking.
            </h1>
            <p className="mt-4 text-base text-anchor-muted max-w-xl leading-relaxed">
              {accountMode === 'paper'
                ? 'Paper account. Here\'s what\'s active, what I\'m watching, and what\'s coming up on the calendar.'
                : 'Here\'s what\'s live, what\'s still on paper, what I\'m watching, and what\'s on the calendar.'}
            </p>
          </>
        )}
      </div>

      {/* ── Two column: Strategies + Pairs ─────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 border-b border-anchor-rule">

        {/* Strategies */}
        <div className="px-8 py-8 md:px-12 lg:border-r border-anchor-rule border-b border-anchor-rule lg:border-b-0">
          <p className="font-mono text-[9px] tracking-[0.32em] uppercase text-anchor-muted/45 mb-7">What I'm running</p>

          <div className="space-y-6">
            <div>
              <p className="font-mono text-[9px] tracking-[0.22em] uppercase text-anchor-muted/40 mb-2">
                {accountMode === 'paper' ? 'Live account' : 'Live now'}
              </p>
              <p className="text-base font-medium text-anchor-text">
                {accountMode === 'paper'
                  ? 'Not connected yet'
                  : liveModes.length > 0 ? liveModes.map(modeLabel).join(', ') : 'Nothing on'}
              </p>
              <p className="text-xs text-anchor-muted mt-1 leading-relaxed">
                {accountMode === 'paper'
                  ? 'No live account connected. Everything is paper.'
                  : liveModes.length > 0
                    ? 'These modes are trading with real money.'
                    : 'No mode is live right now.'}
              </p>
            </div>

            <div className="pt-4 border-t border-anchor-rule/40">
              <p className="font-mono text-[9px] tracking-[0.22em] uppercase text-anchor-muted/40 mb-2">
                {accountMode === 'paper' ? 'On paper' : 'Paper only'}
              </p>
              <p className="text-base font-medium text-anchor-text">
                {paperModes.length > 0 ? paperModes.map(modeLabel).join(', ') : 'Nothing'}
              </p>
              <p className="text-xs text-anchor-muted mt-1 leading-relaxed">
                {accountMode === 'paper'
                  ? paperModes.length > 0
                    ? 'These modes are being tested on the practice account.'
                    : 'No paper-only mode is turned on.'
                  : paperModes.length > 0
                    ? 'These modes are still blocked from live trading.'
                    : 'No mode is still paper-only.'}
              </p>
            </div>

            <div className="pt-4 border-t border-anchor-rule/40">
              <p className="font-mono text-[9px] tracking-[0.22em] uppercase text-anchor-muted/40 mb-2">Pairs I trade</p>
              <p className="text-base font-medium text-anchor-text">
                {pairUniverse.length > 0
                  ? pairUniverse.map(p => p.replace('_', '/')).join(', ')
                  : 'Unavailable'}
              </p>
            </div>

            <div className="pt-4 border-t border-anchor-rule/40">
              <p className="font-mono text-[9px] tracking-[0.22em] uppercase text-anchor-muted/40 mb-2">Today</p>
              <p className="text-base font-medium text-anchor-text">
                {todayData?.signals_today ?? '—'} checks · {todayData?.trades_today ?? '—'} trades
              </p>
            </div>
          </div>
        </div>

        {/* Pairs under watch */}
        <div className="px-8 py-8 md:px-12">
          <p className="font-mono text-[9px] tracking-[0.32em] uppercase text-anchor-muted/45 mb-7">Pair status</p>

          {pairStatuses?.pairs?.length ? (
            <div className="space-y-5">
              {pairStatuses.pairs.map(pair => (
                <div key={pair.instrument} className="pb-5 border-b border-anchor-rule/40 last:border-0 last:pb-0">
                  <div className="flex items-baseline justify-between gap-3 mb-1.5">
                    <p className="text-sm font-semibold text-anchor-text">
                      {pair.instrument.replace('_', '/')}
                    </p>
                    <div className="flex items-center gap-3">
                      <p className="text-xs text-anchor-muted">{pair.metrics.live_trades} trades</p>
                      <p className={`font-mono text-[9px] tracking-[0.1em] uppercase ${
                        pair.status === 'active' ? 'text-anchor-green' :
                        pair.status === 'watchlist' ? 'text-anchor-amber' :
                        'text-anchor-red'
                      }`}>
                        {pair.status === 'active' ? 'active' : pair.status === 'watchlist' ? 'watch' : 'off'}
                      </p>
                    </div>
                  </div>
                  {pair.reasons.length > 0 && (
                    <p className="text-xs text-anchor-muted leading-relaxed">
                      {pair.reasons.map(formatReason).join(' · ')}
                    </p>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-anchor-muted">Pair watch is unavailable.</p>
          )}

          {latestPairSnapshot && (
            <p className="mt-6 font-mono text-[9px] text-anchor-muted/40">
              Last checked {ageText(latestPairSnapshot.evaluated_at)}
            </p>
          )}
        </div>
      </div>

      {/* ── Two column: Bot + market ────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 border-b border-anchor-rule">

        {/* Bot & feed status */}
        <div className="px-8 py-8 md:px-12 lg:border-r border-anchor-rule border-b border-anchor-rule lg:border-b-0">
          <p className="font-mono text-[9px] tracking-[0.32em] uppercase text-anchor-muted/45 mb-7">System</p>

          <div className="space-y-5">
            <div>
              <p className="font-mono text-[9px] tracking-[0.22em] uppercase text-anchor-muted/40 mb-2">
                {accountMode === 'paper' ? 'Practice account' : 'Account'}
              </p>
              <div className="flex items-center gap-2">
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                  !health ? 'bg-anchor-muted/30' :
                  health.status !== 'ok' ? 'bg-anchor-red' :
                  feedOk ? 'bg-anchor-green' : 'bg-anchor-amber'
                }`} />
                <p className="text-base font-medium text-anchor-text">
                  {!health ? 'Checking' :
                   health.status !== 'ok' ? 'Needs work' :
                   !health.stream_connected ? 'Feed down' :
                   feedAgeSeconds != null && feedAgeSeconds > 20 ? 'Feed is slow' :
                   accountMode === 'paper' ? 'Practice mode' : 'Good'}
                </p>
              </div>
              <p className="text-xs text-anchor-muted mt-1 leading-relaxed">
                {health
                  ? `Feed ${health.stream_connected ? 'connected' : 'down'}. Last account sync ${ageText(health.last_reconciliation)}.`
                  : 'Health has not loaded yet.'}
              </p>
            </div>

            <div className="pt-4 border-t border-anchor-rule/40">
              <p className="font-mono text-[9px] tracking-[0.22em] uppercase text-anchor-muted/40 mb-2">Browser connection</p>
              <div className="flex items-center gap-2">
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${wsConnected ? 'bg-anchor-green' : 'bg-anchor-red'}`} />
                <p className="text-base font-medium text-anchor-text">
                  {wsConnected ? 'Connected' : 'Disconnected'}
                </p>
              </div>
              <p className="text-xs text-anchor-muted mt-1">Last heartbeat {ageText(lastHeartbeat)}.</p>
            </div>

            <div className="pt-4 border-t border-anchor-rule/40">
              <p className="font-mono text-[9px] tracking-[0.22em] uppercase text-anchor-muted/40 mb-2">Trading hours (ET)</p>
              <div className="space-y-1.5">
                <div className="flex gap-3">
                  <p className="text-xs text-anchor-muted w-24">Trend</p>
                  <p className="text-xs text-anchor-text">3:15 AM to 8:00 AM</p>
                </div>
                <div className="flex gap-3">
                  <p className="text-xs text-anchor-muted w-24">London Close</p>
                  <p className="text-xs text-anchor-text">1:00 PM to 3:59 PM</p>
                </div>
                <div className="flex gap-3">
                  <p className="text-xs text-anchor-muted w-24">MR / M15</p>
                  <p className="text-xs text-anchor-muted">Off right now</p>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Market events */}
        <div className="px-8 py-8 md:px-12">
          <p className="font-mono text-[9px] tracking-[0.32em] uppercase text-anchor-muted/45 mb-7">Calendar</p>

          {events.length > 0 ? (
            <div className="space-y-5">
              {events.map((event, i) => (
                <div key={`${event.event_name}-${i}`} className="pb-5 border-b border-anchor-rule/40 last:border-0 last:pb-0">
                  <div className="flex items-baseline justify-between gap-3 mb-1">
                    <p className="text-sm font-medium text-anchor-text">{event.event_name}</p>
                    <p className={`font-mono text-[9px] tracking-[0.1em] uppercase shrink-0 ${
                      event.impact === 'HIGH' ? 'text-anchor-red' : 'text-anchor-amber'
                    }`}>
                      {event.impact.toLowerCase()}
                    </p>
                  </div>
                  <p className="text-xs text-anchor-muted">
                    {event.currency} · {new Date(event.event_time).toLocaleString('en-US', {
                      timeZone: 'America/New_York',
                      weekday: 'short',
                      month: 'short',
                      day: 'numeric',
                      hour: 'numeric',
                      minute: '2-digit',
                      hour12: true,
                    })} ET
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-anchor-muted">No upcoming news loaded.</p>
          )}
        </div>
      </div>
    </div>
  )
}
