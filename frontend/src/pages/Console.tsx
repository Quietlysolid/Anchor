import { useMemo } from 'react'
import { EquityCurve } from '../components/charts/EquityCurve'
import { AnimatedNumber } from '../components/ui/AnimatedNumber'
import { useEquityCurve, useLatestPerfCheck, useRolloutConfig, useSystemEvents, useTradeJournal, useSystemHealth } from '../api/hooks'
import { usePositionStore, useSignalStore, useSystemStore } from '../store'
import type { Signal } from '../types'

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

function formatCurrency(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '—'
  return `${value < 0 ? '-' : ''}$${Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`
}

function formatRatioPct(value: number | null | undefined, digits = 0): string {
  if (value == null || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

function formatStoredPct(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return '—'
  return `${value.toFixed(digits)}%`
}

function formatInstrument(instrument: string): string {
  return instrument.replace('_', '/')
}

function plainReason(reason: string | null | undefined): string {
  if (!reason) return 'No reason saved.'
  const raw = reason.toUpperCase()
  if (raw.includes('OFF WINDOW') || raw.includes('OUTSIDE') || raw.includes('WINDOW')) return 'Window wasn\'t open.'
  if (raw.includes('SPREAD')) return 'Spread too wide.'
  if (raw.includes('NEWS') || raw.includes('ECON')) return 'Too close to news.'
  if (raw.includes('HALT') || raw.includes('DISABLED') || raw.includes('PAUSE')) return 'I was paused.'
  if (raw.includes('RISK') || raw.includes('DRAWDOWN') || raw.includes('LOSS')) return 'Risk already high.'
  if (raw.includes('CONFIDENCE') || raw.includes('SCORE') || raw.includes('WEAK')) return 'Setup wasn\'t there.'
  if (raw.includes('CORRELATION')) return 'Too correlated.'
  if (raw.includes('VOLATILE') || raw.includes('REGIME')) return 'Conditions changed.'
  if (raw.includes('HMM') || raw.includes('RANGING')) return 'Still ranging.'
  if (raw.includes('ENTRY')) return 'No entry.'
  return `${raw.replace(/_/g, ' ').toLowerCase()}.`
}

function marketRead(state: string | null | undefined): string {
  if (state === 'TRENDING') return 'looks clean'
  if (state === 'RANGING') return 'is ranging'
  if (state === 'VOLATILE') return 'is moving too fast'
  return 'is being checked'
}

function pairStateNote(state: string | null | undefined): string {
  if (state === 'TRENDING') return 'Clean.'
  if (state === 'RANGING') return 'Ranging.'
  if (state === 'VOLATILE') return 'Too fast.'
  return 'Checking.'
}

function signalEntry(signal: Signal) {
  if (signal.suppressed) {
    return {
      title: `${formatInstrument(signal.instrument)} — passed on it.`,
      detail: plainReason(signal.suppression_reason),
      tone: 'warn' as const,
      time: signal.created_at,
    }
  }
  return {
    title: `${formatInstrument(signal.instrument)} — ${signal.direction === 'LONG' ? 'bought.' : 'sold.'}`,
    detail: `All filters passed. Market ${marketRead(signal.regime_state)}.`,
    tone: 'good' as const,
    time: signal.created_at,
  }
}

function toneClass(tone: 'normal' | 'good' | 'warn' | 'bad') {
  if (tone === 'good') return 'text-anchor-green'
  if (tone === 'warn') return 'text-anchor-amber'
  if (tone === 'bad') return 'text-anchor-red'
  return 'text-anchor-text'
}

function fmtTime(isoStr: string | null | undefined): string {
  if (!isoStr) return ''
  return new Date(isoStr).toLocaleString('en-US', {
    timeZone: 'America/New_York',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  })
}

export default function Console() {
  const { data: equityData } = useEquityCurve()
  const { data: apiTrades } = useTradeJournal()
  const { data: health } = useSystemHealth()
  const { data: rolloutConfig } = useRolloutConfig()
  const { data: perfCheck } = useLatestPerfCheck()
  const { data: systemEvents } = useSystemEvents('WARNING,ERROR,INFO', 10)
  const signals = useSignalStore(s => s.signals)
  const positions = usePositionStore(s => s.positions)
  const { equity, lastHeartbeat, currentRegime } = useSystemStore()

  const equityPts = equityData ?? []
  const trades = apiTrades ?? []
  const latestPoint = equityPts[equityPts.length - 1]
  const liveEquity = equity > 0 ? equity : (health?.account_equity ?? latestPoint?.account_equity ?? 0)
  const todayPL = health?.today_pl ?? null
  const accountMode = health?.account_mode ?? rolloutConfig?.account_mode ?? 'paper'
  const feedAgeSeconds = lastHeartbeat ? Math.max(0, Math.round((Date.now() - lastHeartbeat.getTime()) / 1000)) : null

  const enabledModes = rolloutConfig
    ? (['trend', 'mean_reversion', 'lcr', 'm15'] as const).filter(key => rolloutConfig[key].enabled)
    : []

  const feedOk = health?.stream_connected && (feedAgeSeconds == null || feedAgeSeconds <= 20)
  const healthOk = !health || health.status === 'ok'

  const headline = !health
    ? 'Still booting up.'
    : health.status !== 'ok'
      ? 'Something\'s wrong.'
      : !health.stream_connected
        ? 'I lost my feed.'
        : feedAgeSeconds != null && feedAgeSeconds > 20
          ? 'My feed is lagging.'
          : accountMode === 'paper' && positions.length > 0
            ? 'I\'m in a paper trade.'
            : accountMode === 'paper'
              ? 'I\'m watching.'
              : positions.length > 0
                ? 'I\'m in a live trade.'
                : 'Nothing yet.'

  const subline = !health
    ? 'Loading health status.'
    : health.status !== 'ok'
      ? 'Health check failed. Take a look.'
      : !health.stream_connected
        ? 'Can\'t see the market right now.'
        : feedAgeSeconds != null && feedAgeSeconds > 20
          ? `Last update ${ageText(lastHeartbeat)}. Something\'s off.`
          : positions.length > 0
            ? `${positions.length} paper trade${positions.length === 1 ? '' : 's'} running. Not real cash.`
            : accountMode === 'paper'
              ? `Running on paper. ${enabledModes.length} mode${enabledModes.length === 1 ? '' : 's'} active.`
              : `${enabledModes.length} mode${enabledModes.length === 1 ? '' : 's'} live. Flat right now.`

  const actionLine = useMemo(() => {
    if (!health || health.status !== 'ok' || !health.stream_connected) return 'Take a look at the bot.'
    if (feedAgeSeconds != null && feedAgeSeconds > 20) return 'Feed needs checking.'
    if (perfCheck?.severity === 'WARNING') return 'Recent trades look off.'
    const warning = (systemEvents ?? []).find(e => e.severity === 'ERROR' || e.severity === 'WARNING')
    if (warning) return warning.severity === 'ERROR' ? 'Something\'s wrong with the machine.' : 'Worth keeping an eye on.'
    if (positions.length > 0) return 'I\'m in a trade. Watching.'
    return 'All clear.'
  }, [feedAgeSeconds, health, perfCheck?.severity, positions.length, systemEvents])

  const actionIsAlert = actionLine !== 'All clear.' && actionLine !== 'I\'m in a trade. Watching.'

  const watchedPairs = useMemo(() => {
    const regimes = Object.entries(currentRegime).slice(0, 5)
    if (regimes.length > 0) {
      return regimes.map(([instrument, regime]) => ({
        instrument,
        state: regime.state as string,
        note: pairStateNote(regime.state),
      }))
    }
    return (rolloutConfig?.instruments ?? []).slice(0, 5).map(instrument => ({
      instrument,
      state: null,
      note: 'Being checked',
    }))
  }, [currentRegime, rolloutConfig?.instruments])

  const recentActivity = useMemo(() => {
    const lines = signals.slice(0, 8).map(signal => {
      const entry = signalEntry(signal)
      return {
        ...entry,
        timeStr: fmtTime(entry.time),
        age: ageText(entry.time),
      }
    })
    if (lines.length > 0) return lines
    return [{
      title: 'I\'m awake. Nothing to report yet.',
      detail: 'No recent activity saved.',
      tone: 'normal' as const,
      time: null,
      timeStr: '',
      age: '',
    }]
  }, [signals])

  const recentTrades = trades.slice(0, 10)
  const recentTradeQuality = useMemo(() => {
    if (recentTrades.length === 0) return null
    const wins = recentTrades.filter(t => t.net_pl > 0).length
    return {
      winRate: wins / recentTrades.length,
      net: recentTrades.reduce((sum, t) => sum + t.net_pl, 0),
      count: recentTrades.length,
    }
  }, [recentTrades])

  return (
    <div className="min-h-screen bg-anchor-void pb-20 md:pb-0">

      {/* ── Hero ─────────────────────────────────────────────── */}
      <div className="relative overflow-hidden px-8 pt-10 pb-9 md:px-12 md:pt-12 border-b border-anchor-rule">

        {/* Content sits above the fish */}
        <div className="relative z-10">
        {/* Scan status line */}
        <div className="flex items-center gap-2.5 mb-8">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${feedOk && healthOk ? 'bg-anchor-green animate-glow-pulse' : 'bg-anchor-amber'}`} />
          <p className="font-mono text-[10px] tracking-[0.22em] text-anchor-muted uppercase">
            Paper account
            {lastHeartbeat && feedOk && (
              <> · Checked {ageText(lastHeartbeat)}</>
            )}
            {lastHeartbeat && !feedOk && (
              <span className="text-anchor-amber"> · Feed is slow</span>
            )}
          </p>
        </div>

        {/* Big headline */}
        <h1 className="font-serif text-[3.6rem] md:text-[5.2rem] leading-[0.9] tracking-[-0.02em] text-anchor-text max-w-3xl">
          {headline}
        </h1>

        <p className="mt-5 text-base md:text-lg text-anchor-muted max-w-xl leading-relaxed">
          {subline}
        </p>

        {/* Action line */}
        <p className={`mt-3 text-sm font-medium ${actionIsAlert ? 'text-anchor-amber' : 'text-anchor-muted/70'}`}>
          {actionLine}
        </p>

        {/* Quick context strip */}
        <div className="mt-9 flex flex-wrap items-center gap-x-8 gap-y-3">
          <div>
            <p className="font-mono text-[9px] tracking-[0.28em] uppercase text-anchor-muted/60 mb-1">Today</p>
            {todayPL != null ? (
              <AnimatedNumber
                value={Math.abs(todayPL)}
                prefix={todayPL >= 0 ? '+$' : '-$'}
                decimals={2}
                className={`text-2xl font-semibold tracking-[-0.04em] ${todayPL >= 0 ? 'text-anchor-green' : 'text-anchor-red'}`}
              />
            ) : (
              <p className="text-2xl font-semibold tracking-[-0.04em] text-anchor-text/30">—</p>
            )}
          </div>

          <div className="w-px h-7 bg-anchor-rule hidden sm:block" />

          <div>
            <p className="font-mono text-[9px] tracking-[0.28em] uppercase text-anchor-muted/60 mb-1">Account</p>
            <p className="text-2xl font-semibold tracking-[-0.04em] text-anchor-text">
              {formatCurrency(liveEquity)}
            </p>
          </div>

          {latestPoint && (
            <>
              <div className="w-px h-7 bg-anchor-rule hidden sm:block" />
              <div>
                <p className="font-mono text-[9px] tracking-[0.28em] uppercase text-anchor-muted/60 mb-1">Off peak</p>
                <p className={`text-2xl font-semibold tracking-[-0.04em] ${latestPoint.drawdown_pct > 8 ? 'text-anchor-amber' : 'text-anchor-text'}`}>
                  {formatStoredPct(latestPoint.drawdown_pct)}
                </p>
              </div>
            </>
          )}

          {positions.length > 0 && (
            <>
              <div className="w-px h-7 bg-anchor-rule hidden sm:block" />
              <div>
                <p className="font-mono text-[9px] tracking-[0.28em] uppercase text-anchor-muted/60 mb-1">Open now</p>
                <p className="text-2xl font-semibold tracking-[-0.04em] text-anchor-green">
                  {positions.length} paper {positions.length === 1 ? 'trade' : 'trades'}
                </p>
              </div>
            </>
          )}
        </div>
        </div>{/* end z-10 content */}
      </div>

      {/* ── Activity + Radar ─────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-[1.35fr_0.65fr] border-b border-anchor-rule">

        {/* Activity feed — margin-timestamp layout */}
        <div className="px-8 py-8 md:px-12 lg:border-r border-anchor-rule">
          <p className="font-mono text-[9px] tracking-[0.32em] uppercase text-anchor-muted/50 mb-7">What I did</p>
          <div className="space-y-6">
            {recentActivity.map((item, i) => (
              <div key={i} className="flex gap-5">
                {/* Time in left margin */}
                <div className="w-[68px] shrink-0 pt-0.5">
                  {item.timeStr && (
                    <p className="font-mono text-[10px] text-anchor-muted/40 leading-none">
                      {item.timeStr.replace(' AM', 'a').replace(' PM', 'p')}
                    </p>
                  )}
                </div>
                {/* Entry with left accent */}
                <div className={`flex-1 pl-4 border-l-[1.5px] ${
                  item.tone === 'good' ? 'border-anchor-green' :
                  item.tone === 'warn' ? 'border-anchor-amber' :
                  'border-anchor-rule'
                }`}>
                  <p className={`text-sm font-medium leading-snug ${toneClass(item.tone)}`}>
                    {item.title}
                  </p>
                  <p className="text-xs text-anchor-muted mt-1 leading-relaxed">{item.detail}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Radar panel */}
        <div className="px-8 py-8 md:px-10 border-t border-anchor-rule lg:border-t-0">
          <p className="font-mono text-[9px] tracking-[0.32em] uppercase text-anchor-muted/50 mb-7">Pairs I'm watching</p>

          {watchedPairs.length > 0 ? (
            <div className="space-y-5">
              {watchedPairs.map((pair, i) => (
                <div key={i} className="flex items-baseline gap-3">
                  <p className="text-sm font-semibold text-anchor-text tracking-[-0.01em]">
                    {formatInstrument(pair.instrument)}
                  </p>
                  <p className="text-xs text-anchor-muted">{pair.note}</p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-anchor-muted">No pairs loaded yet. Give me a second.</p>
          )}

          {/* Open positions */}
          {positions.length > 0 && (
            <div className="mt-8 pt-6 border-t border-anchor-rule">
              <p className="font-mono text-[9px] tracking-[0.32em] uppercase text-anchor-muted/50 mb-4">I'm in this</p>
              <div className="space-y-3">
                {positions.map(pos => (
                  <div key={pos.id} className="flex items-baseline justify-between gap-3">
                    <div>
                      <span className="text-sm font-semibold text-anchor-green">
                        {formatInstrument(pos.instrument)}
                      </span>
                      <span className="text-xs text-anchor-muted ml-2">
                        {pos.direction === 'LONG' ? 'buy' : 'sell'}
                      </span>
                    </div>
                    {pos.unrealized_pl != null && (
                      <p className={`text-xs font-mono ${pos.unrealized_pl >= 0 ? 'text-anchor-green' : 'text-anchor-red'}`}>
                        {pos.unrealized_pl >= 0 ? '+' : ''}{pos.unrealized_pl.toFixed(2)}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Bottom info strip ────────────────────────────────── */}
      <div className="px-8 py-6 md:px-12 flex flex-wrap gap-x-10 gap-y-4 border-b border-anchor-rule">
        <div>
          <p className="font-mono text-[9px] tracking-[0.28em] uppercase text-anchor-muted/45 mb-1.5">Trend window</p>
          <p className="text-sm text-anchor-text">3:15 – 8:00 AM</p>
        </div>
        <div>
          <p className="font-mono text-[9px] tracking-[0.28em] uppercase text-anchor-muted/45 mb-1.5">LCR window</p>
          <p className="text-sm text-anchor-text">1:00 – 3:59 PM</p>
        </div>
        <div>
          <p className="font-mono text-[9px] tracking-[0.28em] uppercase text-anchor-muted/45 mb-1.5">Last {recentTradeQuality?.count ?? 10} trades</p>
          <p className={`text-sm ${recentTradeQuality ? (recentTradeQuality.net > 0 ? 'text-anchor-green' : 'text-anchor-red') : 'text-anchor-muted'}`}>
            {recentTradeQuality
              ? `${formatRatioPct(recentTradeQuality.winRate, 0)} win rate · ${formatCurrency(recentTradeQuality.net)}`
              : 'No closed trades yet'}
          </p>
        </div>
        <div>
          <p className="font-mono text-[9px] tracking-[0.28em] uppercase text-anchor-muted/45 mb-1.5">Feed</p>
          <p className={`text-sm ${feedOk ? 'text-anchor-green' : 'text-anchor-amber'}`}>
            {feedOk ? 'Good' : 'Late'}
            {lastHeartbeat && (
              <span className="text-anchor-muted/50 ml-2 font-mono text-[10px]">
                {ageText(lastHeartbeat)}
              </span>
            )}
          </p>
        </div>
        {enabledModes.length > 0 && (
          <div>
            <p className="font-mono text-[9px] tracking-[0.28em] uppercase text-anchor-muted/45 mb-1.5">Running</p>
            <p className="text-sm text-anchor-text">
              {enabledModes.map(m =>
                m === 'mean_reversion' ? 'MR' :
                m === 'lcr' ? 'LCR' :
                m === 'm15' ? 'M15' :
                'Trend'
              ).join(', ')}
            </p>
          </div>
        )}
      </div>

      {/* ── Equity curve ────────────────────────────────────── */}
      {equityPts.length > 0 && (
        <div className="px-8 py-8 md:px-12">
          <EquityCurve data={equityPts} />
        </div>
      )}
    </div>
  )
}
