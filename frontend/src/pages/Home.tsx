import { useEffect, useMemo, useState } from 'react'
import {
  ArrowUpRight,
  CalendarClock,
  ChevronRight,
} from 'lucide-react'
import {
  useHomepageSnapshot,
} from '../api/hooks'
import type { Direction, HomepageSnapshotActivity, OperatorState } from '../types'

type ActivityItem = {
  id: string
  time: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  title: string
  detail: string
  badge: string
}

function collapseRenderedActivity(items: ActivityItem[]) {
  const collapsed: ActivityItem[] = []

  for (const item of items) {
    const last = collapsed[collapsed.length - 1]
    if (
      last &&
      last.title === item.title &&
      last.detail === item.detail &&
      formatRelative(last.time, Date.now()) === formatRelative(item.time, Date.now())
    ) {
      const currentCount = Number(last.badge.match(/x(\d+)/)?.[1] ?? 1)
      last.badge = `${last.badge.replace(/\s*x\d+$/, '')} x${currentCount + 1}`
      continue
    }
    collapsed.push({ ...item })
  }

  return collapsed
}

const ET_ZONE = 'America/New_York'

function cn(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(' ')
}

function instrumentLabel(instrument: string) {
  return instrument.replace('_', '/')
}

function formatCurrency(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) return '—'
  return `${value < 0 ? '-' : ''}$${Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`
}

function formatPrice(value: number | null | undefined, digits = 5) {
  if (value == null || Number.isNaN(value)) return '—'
  return value.toFixed(digits)
}

function formatUnits(value: number) {
  return Math.abs(value).toLocaleString('en-US')
}

function formatRelative(isoStr: string | null | undefined, nowMs: number) {
  if (!isoStr) return 'unknown'
  const deltaSecs = Math.max(0, Math.round((nowMs - new Date(isoStr).getTime()) / 1000))
  if (deltaSecs < 10) return 'just now'
  if (deltaSecs < 60) return `${deltaSecs}s ago`
  const mins = Math.floor(deltaSecs / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

function formatET(isoStr: string | null | undefined, opts: Intl.DateTimeFormatOptions) {
  if (!isoStr) return '—'
  return new Date(isoStr).toLocaleString('en-US', { timeZone: ET_ZONE, ...opts })
}

function formatEventTime(isoStr: string) {
  return formatET(isoStr, { weekday: 'short', hour: 'numeric', minute: '2-digit', hour12: true })
}

function formatRelativeDayTime(isoStr: string | null | undefined) {
  if (!isoStr) return '—'
  const target = new Date(isoStr)
  const now = new Date()

  const targetDay = new Date(target.toLocaleString('en-US', { timeZone: ET_ZONE }))
  const nowDay = new Date(now.toLocaleString('en-US', { timeZone: ET_ZONE }))

  const targetMidnight = new Date(targetDay)
  targetMidnight.setHours(0, 0, 0, 0)
  const nowMidnight = new Date(nowDay)
  nowMidnight.setHours(0, 0, 0, 0)

  const dayDiff = Math.round((targetMidnight.getTime() - nowMidnight.getTime()) / 86_400_000)
  const timePart = formatET(isoStr, { hour: 'numeric', minute: '2-digit', hour12: true })

  if (dayDiff === 0) return `today by ${timePart}`
  if (dayDiff === 1) return `tomorrow by ${timePart}`
  if (dayDiff === -1) return `yesterday by ${timePart}`
  return formatET(isoStr, { weekday: 'short', hour: 'numeric', minute: '2-digit', hour12: true })
}

function toneAccent(tone: ActivityItem['tone']): string {
  if (tone === 'good') return '#2d6a4f'
  if (tone === 'warn') return '#7a5c1e'
  if (tone === 'bad')  return '#8b2020'
  return '#2b5ea7'
}

function directionText(direction: Direction) {
  return direction === 'LONG' ? 'long' : 'short'
}

function snapshotActivityNarration(item: HomepageSnapshotActivity): ActivityItem {
  return {
    id: item.id,
    time: item.occurred_at,
    tone: item.tone,
    badge: item.badge,
    title: item.kind === 'signal_blocked_batch'
      ? `${item.badge} because ${plainReason(item.reason_code)}`
      : item.instrument && item.kind === 'signal_blocked'
      ? `I see something for ${instrumentLabel(item.instrument)}, but ${plainReason(item.reason_code)}`
      : item.instrument && item.kind === 'signal_passed'
        ? `I saw a clean ${item.detail.toLowerCase()} on ${instrumentLabel(item.instrument)}.`
        : item.instrument && item.kind === 'order'
          ? `${instrumentLabel(item.instrument)} order update: ${item.badge.toLowerCase()}.`
          : item.instrument && item.kind === 'trade_closed'
            ? `${instrumentLabel(item.instrument)} don close.`
            : humanEventTitle(item.reason_code, item.title),
    detail: item.kind === 'signal_blocked_batch'
      ? `Affected pairs: ${item.detail}`
      : item.instrument && item.kind === 'signal_blocked'
      ? 'I no force setups when the conditions never line up properly.'
      : humanEventDetail(item.reason_code, item.detail),
  }
}

function plainReason(reason: string | null | undefined) {
  if (!reason) return 'the setup no fully qualify, so I leave am'
  const raw = reason.toUpperCase()
  if (raw.includes('WINDOW') || raw.includes('OUTSIDE')) return 'trading window block me, so I leave am'
  if (raw.includes('SPREAD')) return 'spread block me, so I leave am'
  if (raw.includes('NEWS') || raw.includes('ECON')) return 'news block me, so I leave am'
  if (raw.includes('HALT') || raw.includes('DISABLED') || raw.includes('PAUSE')) return 'I pause that side, so I leave am'
  if (raw.includes('RISK') || raw.includes('LOSS') || raw.includes('DRAWDOWN')) return 'risk block me, so I leave am'
  if (raw.includes('FRAGILE_ROBUSTNESS')) return 'this pair never convince me yet, so I leave am'
  if (raw.includes('CONFIDENCE') || raw.includes('WEAK') || raw.includes('SCORE')) return 'the setup no strong enough, so I leave am'
  if (raw.includes('OVERLAP')) return 'session no favour this setup, so I leave am'
  if (raw.includes('RANGING') || raw.includes('HMM') || raw.includes('VOLATILE')) return 'market no clear well, so I leave am'
  return 'conditions never line up well, so I leave am'
}

function humanEventTitle(reasonCode: string | null | undefined, fallback: string) {
  const raw = (reasonCode ?? '').toUpperCase()
  if (raw === 'RECONCILIATION') return 'I balanced everything again.'
  if (raw === 'LIVE_PERF_CHECK') return 'I checked how recent performance dey go.'
  if (raw === 'EDGE_CONFIDENCE_CHECK') return 'I checked whether my edge still strong.'
  return fallback
}

function humanEventDetail(reasonCode: string | null | undefined, fallback: string) {
  const raw = (reasonCode ?? '').toUpperCase()
  if (raw === 'RECONCILIATION') return 'Account, positions, and orders don line up again.'
  if (raw === 'LIVE_PERF_CHECK') return 'I reviewed the recent trading stretch for drift or weakness.'
  if (raw === 'EDGE_CONFIDENCE_CHECK') return 'I checked whether market conditions still suit the system.'
  return fallback
}

function operatorHeadline(operatorState: OperatorState | undefined) {
  if (!operatorState) return "I'm waking up."
  if (operatorState.operator_state === 'MANAGING_POSITIONS') {
    return `I'm managing ${operatorState.open_positions_count} trade${operatorState.open_positions_count === 1 ? '' : 's'} right now.`
  }
  if (operatorState.operator_state === 'WAITING_ON_ORDERS') {
    return `I'm waiting on ${operatorState.working_orders_count} order${operatorState.working_orders_count === 1 ? '' : 's'} to fill.`
  }
  if (operatorState.operator_state === 'SCANNING' && operatorState.active_window) {
    return `I'm watching ${operatorState.active_window.label}.`
  }
  if (operatorState.operator_state === 'DEGRADED') return 'I need your eye right now.'
  if (operatorState.next_window) return `I'm waiting for the ${operatorState.next_window.label} window.`
  return "I'm standing by."
}

function operatorDetail(operatorState: OperatorState | undefined, streamConnected?: boolean) {
  if (!operatorState) return "I'm still booting up and pulling my bearings."
  if (operatorState.operator_state === 'DEGRADED') {
    if (streamConnected === false) return "My market feed is down, so I can't trade yet."
    return 'Something for the system stack needs attention.'
  }
  if (operatorState.operator_state === 'MANAGING_POSITIONS') {
    return `I'm managing ${operatorState.open_positions_count} open position${operatorState.open_positions_count === 1 ? '' : 's'} for you.`
  }
  if (operatorState.operator_state === 'WAITING_ON_ORDERS') {
    return `I get ${operatorState.working_orders_count} order${operatorState.working_orders_count === 1 ? '' : 's'} wey still dey work for market.`
  }
  if (operatorState.operator_state === 'SCANNING' && operatorState.active_window) {
    return `Window don open. I'm looking for something clean to take.`
  }
  if (operatorState.next_window) {
    return `Nothing dey open yet. I go check again ${formatRelativeDayTime(operatorState.next_window.starts_at)}.`
  }
  return 'No active trading window dey enabled right now.'
}

// ─── Chart Inset Card ────────────────────────────────────────────────────────

function SectionCard({
  id,
  title,
  children,
  className,
  defaultOpen = true,
}: {
  id?: string
  title: string
  children: React.ReactNode
  className?: string
  defaultOpen?: boolean
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen)

  return (
    <section
      id={id}
      className={cn('border border-anchor-navy/20 bg-anchor-card', className)}
    >
      <button
        type="button"
        onClick={() => setIsOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left md:px-7 md:pt-6"
        aria-expanded={isOpen}
      >
        <h2 className="font-display text-xl text-anchor-navy">{title}</h2>
        <ChevronRight
          size={15}
          className={cn(
            'shrink-0 text-anchor-rule transition-transform duration-200',
            isOpen ? 'rotate-90' : 'rotate-0',
          )}
        />
      </button>
      <div className="mx-5 h-px bg-anchor-rule/60 md:mx-7" />
      {isOpen && <div className="px-5 pb-5 pt-4 md:px-7 md:pb-7">{children}</div>}
    </section>
  )
}

function EmptyCard({ text }: { text: string }) {
  return (
    <div className="border border-dashed border-anchor-rule/60 p-4 text-sm leading-6 text-anchor-fog">
      {text}
    </div>
  )
}

// ─── Anchor Mark SVG ─────────────────────────────────────────────────────────

function AnchorMark({ size = 16, className = '' }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" className={className}>
      <circle cx="10" cy="4"  r="2.2" stroke="currentColor" strokeWidth="1.5"/>
      <line x1="10" y1="6.2"  x2="10"  y2="17"  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      <line x1="5"  y1="8.8"  x2="15"  y2="8.8"  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      <path d="M10 17 Q 5.5 17.5 4.5 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none"/>
      <path d="M10 17 Q 14.5 17.5 15.5 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none"/>
    </svg>
  )
}

// ─── Page ────────────────────────────────────────────────────────────────────

export default function Home() {
  const [now, setNow] = useState(() => new Date())
  const { data: snapshot } = useHomepageSnapshot()
  const operatorState = snapshot?.operator
  const account = snapshot?.account

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30_000)
    return () => window.clearInterval(timer)
  }, [])

  const positions        = snapshot?.exposure.positions ?? []
  const pendingOrders    = snapshot?.exposure.orders    ?? []
  const trades           = snapshot?.recent_results     ?? []
  const upcomingEvents   = snapshot?.calendar           ?? []
  const runningStrategies = (snapshot?.strategies ?? []).filter((s) => s.status === 'running')
  const enabledStrategies = (snapshot?.strategies ?? []).filter((s) => s.status === 'enabled')
  const offStrategies     = (snapshot?.strategies ?? []).filter((s) => s.status === 'off')
  const heroTitle        = operatorHeadline(operatorState)
  const criticalMessage  = operatorDetail(operatorState, account?.stream_connected)

  const activityItems = useMemo(
    () => collapseRenderedActivity((snapshot?.activity ?? []).map(snapshotActivityNarration)).slice(0, 6),
    [snapshot?.activity],
  )

  const watchlist = useMemo(
    () =>
      (snapshot?.watchlist ?? []).map((item) => ({
        instrument: item.instrument,
        status:     item.status,
        note:
          item.reason_codes.length > 0
            ? plainReason(item.reason_codes[0])
            : item.regime === 'TRENDING'
              ? 'This one dey line up nicely.'
              : item.regime === 'RANGING'
                ? "Range still tight. I'm not forcing it."
                : item.regime === 'VOLATILE'
                  ? "Movement rough, so I'm gentle here."
                  : "I'm still sizing this one up.",
        confidence: item.confidence,
        regime:     item.regime,
      })),
    [snapshot?.watchlist],
  )

  return (
    <div className="mx-auto max-w-7xl px-4 pb-16 pt-6 sm:px-6 md:px-8">

      {/* ── Chart Masthead ── */}
      <header className="mb-4 border border-anchor-navy/20 bg-anchor-card">

        {/* Top rule: brand + sync status */}
        <div className="flex items-center justify-between border-b border-anchor-rule/60 px-5 py-3 md:px-7">
          <div className="flex items-center gap-2.5">
            <AnchorMark size={14} className="text-anchor-navy/50" />
            <span className="font-mono text-[9px] tracking-[0.28em] uppercase text-anchor-navy/50">
              Anchor · Algo FX
            </span>
          </div>
          <span className="font-mono text-[9px] tracking-[0.2em] uppercase text-anchor-fog">
            {account?.last_reconciliation
              ? `sync ${formatRelative(account.last_reconciliation, now.getTime())}`
              : 'syncing...'}
          </span>
        </div>

        {/* Hero body */}
        <div className="grid gap-5 p-5 md:p-7 lg:grid-cols-[1.45fr_0.9fr] lg:items-start">

          {/* Left: operator headline */}
          <div>
            <p className="mb-3 font-mono text-[9px] tracking-[0.22em] uppercase text-anchor-fog">
              Status
            </p>
            <h1 className="font-display text-[3rem] leading-[1.05] text-anchor-navy sm:text-[3.8rem] md:text-[4.5rem]">
              {heroTitle}
            </h1>
            <p className="mt-4 max-w-2xl text-base leading-7 text-anchor-slate md:text-lg">
              {criticalMessage}
            </p>

            {/* Strategy tags */}
            {(runningStrategies.length > 0 || enabledStrategies.length > 0 || offStrategies.length > 0) && (
              <div className="mt-5 space-y-2.5">
                {runningStrategies.length > 0 && (
                  <div>
                    <p className="mb-1.5 font-mono text-[9px] tracking-[0.2em] uppercase text-anchor-fog">Running now</p>
                    <div className="flex flex-wrap gap-2">
                      {runningStrategies.map((s) => (
                        <span key={s.engine} className="border border-anchor-win/40 px-2.5 py-1 font-mono text-[9px] tracking-[0.14em] uppercase text-anchor-win">
                          {s.label} · {s.readiness_label}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {enabledStrategies.length > 0 && (
                  <div>
                    <p className="mb-1.5 font-mono text-[9px] tracking-[0.2em] uppercase text-anchor-fog">Enabled, waiting</p>
                    <div className="flex flex-wrap gap-2">
                      {enabledStrategies.map((s) => (
                        <span key={s.engine} className="border border-anchor-chartblue/40 px-2.5 py-1 font-mono text-[9px] tracking-[0.14em] uppercase text-anchor-chartblue">
                          {s.label} · {s.readiness_label}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {offStrategies.length > 0 && (
                  <div>
                    <p className="mb-1.5 font-mono text-[9px] tracking-[0.2em] uppercase text-anchor-fog">Off</p>
                    <div className="flex flex-wrap gap-2">
                      {offStrategies.map((s) => (
                        <span key={s.engine} className="border border-anchor-rule/70 px-2.5 py-1 font-mono text-[9px] tracking-[0.14em] uppercase text-anchor-fog">
                          {s.label} · {s.readiness_label}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Right: inset data panels */}
          <div className="space-y-2.5">

            {/* Next window */}
            <div className="border border-anchor-rule/70 bg-anchor-parchment/50 p-4">
              <p className="mb-2 font-mono text-[9px] tracking-[0.2em] uppercase text-anchor-fog">Next Window</p>
              <p className="text-sm leading-6 text-anchor-navy">
                {operatorState?.next_window
                  ? `${operatorState.next_window.label} opens ${formatRelativeDayTime(operatorState.next_window.starts_at)}.`
                  : operatorState?.active_window
                    ? `${operatorState.active_window.label} is running now.`
                    : 'Waiting for backend state.'}
              </p>
            </div>

            {/* Account */}
            <div className="border border-anchor-rule/70 bg-anchor-parchment/50 p-4">
              <p className="mb-2 font-mono text-[9px] tracking-[0.2em] uppercase text-anchor-fog">Account</p>
              <p className="font-mono text-2xl text-anchor-navy">{formatCurrency(account?.equity ?? null)}</p>
              <p className="mt-1.5 text-sm text-anchor-slate">
                {account?.today_pl && account.today_pl !== 0
                  ? `Today ${account.today_pl > 0 ? '+' : ''}${formatCurrency(account.today_pl)}`
                  : `Balance ${formatCurrency(account?.balance ?? null)}`}
              </p>
            </div>

            {/* Mode */}
            <div className="border border-anchor-rule/70 bg-anchor-parchment/50 px-4 py-3">
              <span className="font-mono text-[9px] tracking-[0.18em] uppercase text-anchor-fog">
                {account?.environment === 'practice' ? 'Fake money, real lessons' : `Live · ${account?.environment ?? 'broker'}`}
              </span>
            </div>
          </div>
        </div>
      </header>

      {/* ── Main grid ── */}
      <div className="grid gap-4 lg:grid-cols-[1.3fr_0.95fr]">

        {/* Left column */}
        <div className="space-y-4">

          {/* Activity log */}
          <SectionCard id="activity" title="What just happened">
            <div className="space-y-2.5">
              {activityItems.length > 0 ? (
                activityItems.map((item) => (
                  <article
                    key={item.id}
                    style={{ borderLeftColor: toneAccent(item.tone), borderLeftWidth: '3px' }}
                    className="border border-anchor-navy/10 bg-anchor-parchment/30 p-4"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-mono text-[9px] tracking-[0.18em] uppercase text-anchor-fog">{item.badge}</p>
                        <p className="mt-2 text-sm font-semibold leading-snug text-anchor-navy">{item.title}</p>
                        <p className="mt-1.5 text-sm leading-6 text-anchor-slate">{item.detail}</p>
                      </div>
                      <p className="shrink-0 font-mono text-[9px] tracking-[0.14em] uppercase text-anchor-fog">
                        {formatRelative(item.time, now.getTime())}
                      </p>
                    </div>
                  </article>
                ))
              ) : (
                <EmptyCard text="No fresh events yet, but I'm still watching the market for you." />
              )}
            </div>
          </SectionCard>

          {/* Open positions & orders */}
          <SectionCard
            id="open"
            title="Open trades and orders"
            defaultOpen={positions.length > 0 || pendingOrders.length > 0}
          >
            <div className="space-y-5">

              <div>
                <p className="mb-3 font-mono text-[9px] tracking-[0.2em] uppercase text-anchor-fog">Open positions</p>
                <div className="space-y-2.5">
                  {positions.length > 0 ? positions.map((pos) => (
                    <div key={pos.id} className="border border-anchor-navy/15 bg-anchor-parchment/30 p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-base font-semibold text-anchor-navy">
                            {instrumentLabel(pos.instrument)}
                            <span className="ml-2 font-mono text-[9px] tracking-[0.14em] uppercase text-anchor-fog">
                              {directionText(pos.direction)}
                            </span>
                          </p>
                          <p className="mt-1 text-sm text-anchor-slate">
                            {formatUnits(pos.units)} units · entry {formatPrice(pos.avg_entry_price)}
                          </p>
                        </div>
                        <p className={cn(
                          'shrink-0 font-mono text-lg font-semibold',
                          (pos.unrealized_pl ?? 0) > 0 ? 'text-anchor-win' : (pos.unrealized_pl ?? 0) < 0 ? 'text-anchor-loss' : 'text-anchor-navy',
                        )}>
                          {formatCurrency(pos.unrealized_pl)}
                        </p>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-1.5">
                        {[
                          `NOW ${formatPrice(pos.current_price)}`,
                          `SL ${formatPrice(pos.stop_loss)}`,
                          `TP ${formatPrice(pos.take_profit)}`,
                        ].map((label) => (
                          <span key={label} className="border border-anchor-rule/60 px-2 py-0.5 font-mono text-[9px] tracking-[0.1em] text-anchor-fog">
                            {label}
                          </span>
                        ))}
                      </div>
                    </div>
                  )) : <EmptyCard text="I'm flat right now. No position dey open." />}
                </div>
              </div>

              <div>
                <p className="mb-3 font-mono text-[9px] tracking-[0.2em] uppercase text-anchor-fog">Pending orders</p>
                <div className="space-y-2.5">
                  {pendingOrders.length > 0 ? pendingOrders.map((order) => (
                    <div key={order.id} className="border border-anchor-navy/15 bg-anchor-parchment/30 p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-base font-semibold text-anchor-navy">
                            {instrumentLabel(order.instrument)}
                            <span className="ml-2 font-mono text-[9px] tracking-[0.12em] uppercase text-anchor-fog">
                              {order.state.toLowerCase()}
                            </span>
                          </p>
                          <p className="mt-1 text-sm text-anchor-slate">
                            {order.order_type} · {directionText(order.direction)} · {formatUnits(order.units)} units
                          </p>
                        </div>
                        <ArrowUpRight size={15} className="shrink-0 text-anchor-rule" />
                      </div>
                    </div>
                  )) : <EmptyCard text="No order is waiting in market right now." />}
                </div>
              </div>
            </div>
          </SectionCard>

          {/* Recent results */}
          <SectionCard id="results" title="How things ended" defaultOpen={false}>
            {trades.length > 0 ? (
              <div>
                {trades.slice(0, 6).map((trade) => {
                  const won = trade.net_pl > 0
                  return (
                    <div key={trade.id} className="border-b border-anchor-rule/40 py-3.5 last:border-0">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-base font-semibold text-anchor-navy">
                            {instrumentLabel(trade.instrument)}
                            <span className={cn(
                              'ml-2 font-mono text-[9px] tracking-[0.14em] uppercase',
                              won ? 'text-anchor-win' : 'text-anchor-loss',
                            )}>
                              {won ? 'closed green' : 'took the hit'}
                            </span>
                          </p>
                          <p className="mt-1 text-sm text-anchor-slate">
                            {directionText(trade.direction)} ·{' '}
                            {formatET(trade.opened_at, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true })}
                            {' '}→ {formatET(trade.closed_at, { hour: 'numeric', minute: '2-digit', hour12: true })}
                          </p>
                        </div>
                        <p className={cn(
                          'shrink-0 font-mono text-lg font-semibold',
                          won ? 'text-anchor-win' : 'text-anchor-loss',
                        )}>
                          {formatCurrency(trade.net_pl)}
                        </p>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <EmptyCard text="No closed trades yet. I'm still writing my track record." />
            )}
          </SectionCard>
        </div>

        {/* Right column */}
        <div className="space-y-4">

          {/* Watchlist */}
          <SectionCard id="watchlist" title="What Anchor is watching">
            {watchlist.length > 0 ? (
              <div>
                {watchlist.map((item) => (
                  <div key={item.instrument} className="border-b border-anchor-rule/40 py-3.5 last:border-0">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-base font-semibold text-anchor-navy">{instrumentLabel(item.instrument)}</p>
                        <p className="mt-1 text-sm leading-5 text-anchor-slate">{item.note}</p>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1.5">
                        <span className={cn(
                          'border px-2 py-0.5 font-mono text-[9px] tracking-[0.14em] uppercase',
                          item.status === 'active'   ? 'border-anchor-win/40 text-anchor-win'
                          : item.status === 'disabled' ? 'border-anchor-loss/40 text-anchor-loss'
                          : 'border-anchor-warn/40 text-anchor-warn',
                        )}>
                          {item.status}
                        </span>
                        {item.regime && (
                          <span className="font-mono text-[9px] tracking-[0.1em] uppercase text-anchor-fog">
                            {item.regime.toLowerCase()}
                          </span>
                        )}
                      </div>
                    </div>
                    {item.confidence != null && (
                      <div className="mt-2.5 flex items-center gap-2">
                        <div className="h-px flex-1 bg-anchor-rule/30" />
                        <span className="font-mono text-[9px] tracking-[0.12em] uppercase text-anchor-fog">
                          {Math.round(item.confidence * 100)}% conf
                        </span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <EmptyCard text="I'm waiting for fresh pair context to load." />
            )}
          </SectionCard>

          {/* Economic calendar */}
          <SectionCard title="What could shake the market" defaultOpen={false}>
            {upcomingEvents.length > 0 ? (
              <div>
                {upcomingEvents.map((event, index) => (
                  <div key={`${event.event_name}-${index}`} className="border-b border-anchor-rule/40 py-3.5 last:border-0">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-anchor-navy">{event.event_name}</p>
                        <p className="mt-1 font-mono text-[9px] tracking-[0.1em] uppercase text-anchor-fog">
                          {event.currency} · {event.impact} impact
                        </p>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1.5">
                        <CalendarClock size={14} className="text-anchor-rule" />
                        <p className="font-mono text-[9px] tracking-[0.1em] uppercase text-anchor-fog">
                          {formatEventTime(event.event_time)}
                        </p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyCard text="No major calendar event is staring me down right now." />
            )}
          </SectionCard>
        </div>
      </div>

      <footer className="mb-4 mt-8 px-1">
        <p className="font-mono text-[9px] tracking-[0.16em] uppercase text-anchor-rule">
          cant believe you can die bro... i thought we had time smh
        </p>
      </footer>
    </div>
  )
}
