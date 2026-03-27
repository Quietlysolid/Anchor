import { useEffect, useMemo, useState, type CSSProperties } from 'react'
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
  if (instrument.includes('_')) return instrument.replace('_', '/')
  const match = instrument.match(/^([A-Z]+)-(\d{4})(\d{2})(\d{2})?$/)
  if (!match) return instrument
  const [, root, year, month] = match
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, 1))
  const monthLabel = date.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' })
  return `${root} ${monthLabel} ${year}`
}

function formatCurrency(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) return '—'
  return `${value < 0 ? '-' : ''}$${Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`
}

function formatSignedCurrency(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) return '—'
  const amount = formatCurrency(value, digits)
  return value > 0 ? `+${amount}` : amount
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
  const isReconciliation = (item.reason_code ?? '').toUpperCase() === 'RECONCILIATION'
  return {
    id: item.id,
    time: item.occurred_at,
    tone: item.tone,
    badge: isReconciliation ? 'SYNC' : item.badge,
    title: item.kind === 'signal_blocked_batch'
      ? `${item.badge} because ${plainReason(item.reason_code)}`
      : item.instrument && item.kind === 'signal_blocked'
      ? `${instrumentLabel(item.instrument)} was skipped.`
      : item.instrument && item.kind === 'signal_passed'
        ? `${instrumentLabel(item.instrument)} is active.`
        : item.instrument && item.kind === 'order'
          ? `${instrumentLabel(item.instrument)} order updated.`
          : item.instrument && item.kind === 'trade_closed'
            ? `${instrumentLabel(item.instrument)} position closed.`
            : humanEventTitle(item.reason_code, item.title),
    detail: item.kind === 'signal_blocked_batch'
      ? `Affected pairs: ${item.detail}`
      : item.instrument && item.kind === 'signal_blocked'
      ? plainReason(item.reason_code)
      : humanEventDetail(item.reason_code, item.detail),
  }
}

function plainReason(reason: string | null | undefined) {
  if (!reason) return 'Conditions did not fully line up.'
  const raw = reason.toUpperCase()
  if (raw.includes('WINDOW') || raw.includes('OUTSIDE')) return 'It was outside the trading window.'
  if (raw.includes('SPREAD')) return 'Spread was too wide.'
  if (raw.includes('NEWS') || raw.includes('ECON')) return 'A macro event was too close.'
  if (raw.includes('HALT') || raw.includes('DISABLED') || raw.includes('PAUSE')) return 'Trading was paused.'
  if (raw.includes('RISK') || raw.includes('LOSS') || raw.includes('DRAWDOWN')) return 'Risk controls blocked it.'
  if (raw.includes('FRAGILE_ROBUSTNESS')) return 'This market is not trusted enough yet.'
  if (raw.includes('CONFIDENCE') || raw.includes('WEAK') || raw.includes('SCORE')) return 'The signal was not strong enough.'
  if (raw.includes('OVERLAP')) return 'Timing was not right for a new trade.'
  if (raw.includes('RANGING') || raw.includes('HMM') || raw.includes('VOLATILE')) return 'Market conditions were unclear.'
  return 'Conditions did not line up clearly.'
}

function humanEventTitle(reasonCode: string | null | undefined, fallback: string) {
  const raw = (reasonCode ?? '').toUpperCase()
  if (raw === 'RECONCILIATION') return 'Portfolio synchronized.'
  if (raw === 'LIVE_PERF_CHECK') return 'Performance check completed.'
  if (raw === 'EDGE_CONFIDENCE_CHECK') return 'Strategy check completed.'
  return fallback
}

function humanEventDetail(reasonCode: string | null | undefined, fallback: string) {
  const raw = (reasonCode ?? '').toUpperCase()
  if (raw === 'RECONCILIATION') return 'Account, positions, and orders match broker state.'
  if (raw === 'LIVE_PERF_CHECK') return 'Recent trading was reviewed for drift or weakness.'
  if (raw === 'EDGE_CONFIDENCE_CHECK') return 'Current conditions were checked against the strategy.'
  return fallback
}

function operatorHeadline(operatorState: OperatorState | undefined) {
  if (!operatorState) return 'Anchor is starting up.'
  if (operatorState.operator_state === 'MANAGING_POSITIONS') {
    return `Anchor is managing ${operatorState.open_positions_count} position${operatorState.open_positions_count === 1 ? '' : 's'} right now.`
  }
  if (operatorState.operator_state === 'WAITING_ON_ORDERS') {
    return `${operatorState.working_orders_count} order${operatorState.working_orders_count === 1 ? '' : 's'} are waiting to fill.`
  }
  if (operatorState.operator_state === 'SCANNING' && operatorState.active_window) {
    return 'Anchor is live and watching the market.'
  }
  if (operatorState.operator_state === 'DEGRADED') return 'Anchor needs attention right now.'
  if (operatorState.next_window) return 'Anchor is standing by for the next rebalance.'
  return 'Anchor is standing by.'
}

function statusPillClass(status: string) {
  if (status === 'active') return 'border-anchor-win/40 bg-anchor-win/10 text-anchor-win'
  if (status === 'disabled') return 'border-anchor-loss/40 bg-anchor-loss/10 text-anchor-loss'
  return 'border-anchor-chartblue/30 bg-anchor-chartblue/10 text-anchor-chartblue'
}

function todayValueStyle(value: number | null | undefined): CSSProperties | undefined {
  if (value == null || Number.isNaN(value) || value === 0) return undefined
  return { color: value > 0 ? '#2F6B57' : '#9B3D36' }
}

function HeroMetric({
  label,
  value,
  subtext,
  valueClassName,
  valueStyle,
}: {
  label: string
  value: string
  subtext?: string
  valueClassName?: string
  valueStyle?: CSSProperties
}) {
  return (
    <div className="panel-glass border border-anchor-navy/[0.06] p-3 shadow-[0_8px_24px_rgba(26,39,68,0.035)] sm:p-4">
      <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog [text-shadow:0_1px_0_rgba(255,255,255,0.7)]">{label}</p>
      <p
        className={cn('mt-2 break-words font-mono text-[1.08rem] leading-tight text-anchor-navy sm:mt-3 sm:text-2xl', valueClassName)}
        style={valueStyle}
      >
        {value}
      </p>
      {subtext ? <p className="mt-2 text-sm leading-6 text-anchor-slate">{subtext}</p> : null}
    </div>
  )
}

function PulseDot({ active }: { active: boolean }) {
  return (
    <span className="relative inline-flex h-2.5 w-2.5">
      <span
        className={cn(
          'absolute inline-flex h-full w-full rounded-full opacity-70',
          active ? 'animate-ping bg-anchor-win/60' : 'bg-anchor-loss/40',
        )}
      />
      <span
        className={cn(
          'relative inline-flex h-2.5 w-2.5 rounded-full',
          active ? 'bg-anchor-win' : 'bg-anchor-loss',
        )}
      />
    </span>
  )
}

function DataBand({
  label,
  value,
  tone = 'neutral',
}: {
  label: string
  value: string
  tone?: 'neutral' | 'good' | 'warn'
}) {
  return (
    <div className="panel-glass min-w-0 border border-anchor-navy/[0.05] px-3 py-2 sm:px-4 sm:py-2.5 shadow-[0_5px_14px_rgba(26,39,68,0.022)]">
      <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-anchor-fog [text-shadow:0_1px_0_rgba(255,255,255,0.7)]">{label}</p>
      <p
        className={cn(
          'mt-1 break-words text-[13px] font-semibold leading-tight sm:mt-1.5 sm:text-sm',
          tone === 'good' ? 'text-anchor-win' : tone === 'warn' ? 'text-anchor-warn' : 'text-anchor-navy',
        )}
      >
        {value}
      </p>
    </div>
  )
}

function PremiumSection({
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
      className={cn(
        'panel-glass overflow-hidden border border-anchor-navy/[0.08] shadow-[0_16px_42px_rgba(26,39,68,0.05)]',
        className,
      )}
    >
      <button
        type="button"
        onClick={() => setIsOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-3 border-b border-anchor-rule/35 px-4 py-3.5 text-left sm:px-5 sm:py-4 md:px-7"
        aria-expanded={isOpen}
      >
        <h2 className="font-display text-[1.2rem] leading-tight text-anchor-navy sm:text-[1.45rem]">{title}</h2>
        <ChevronRight
          size={14}
          className={cn(
            'shrink-0 text-anchor-rule/75 transition-transform duration-200',
            isOpen ? 'rotate-90' : 'rotate-0',
          )}
        />
      </button>
      {isOpen && <div className="px-4 pb-5 pt-4 sm:px-5 md:px-7 md:pb-7">{children}</div>}
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
    document.title = 'Anchor Futures — Systematic Trading Console'
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30_000)
    return () => window.clearInterval(timer)
  }, [])

  const positions        = snapshot?.exposure.positions ?? []
  const pendingOrders    = snapshot?.exposure.orders    ?? []
  const trades           = snapshot?.recent_results     ?? []
  const upcomingEvents   = snapshot?.calendar           ?? []
  const heroTitle        = operatorHeadline(operatorState)
  const statusTone = account?.stream_connected ? 'good' : 'warn'

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
          item.reason_codes.includes('signal_long')
            ? 'Model wants this market long in the current basket.'
            : item.reason_codes.includes('signal_short')
              ? 'Model wants this market short in the current basket.'
              : item.reason_codes.includes('flat_signal')
                ? "Signal is flat here, so I'm leaving it out."
                : item.reason_codes.length > 0
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

  const strategyLabel = snapshot?.strategies?.find((strategy) => strategy.status === 'running')?.label
    ?? snapshot?.strategies?.[0]?.label
    ?? 'Trend strategy'

  return (
    <div className="mx-auto max-w-6xl px-3 pb-16 pt-3 sm:px-6 sm:pt-4 md:px-8">
      <header className="panel-glass relative mb-6 overflow-hidden border border-anchor-navy/[0.1] shadow-[0_18px_56px_rgba(26,39,68,0.07)]">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-anchor-chartblue via-anchor-win to-anchor-warn" />
        <div className="pointer-events-none absolute right-0 top-0 h-44 w-44 rounded-full bg-anchor-chartblue/10 blur-3xl" />
        <div className="pointer-events-none absolute bottom-0 left-0 h-32 w-32 rounded-full bg-anchor-win/10 blur-3xl" />

        <div className="border-b border-anchor-rule/30 px-3 py-2.5 sm:px-5 sm:py-3 md:px-6">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <AnchorMark size={14} className="text-anchor-navy/55" />
              <span className="font-mono text-[9px] uppercase tracking-[0.26em] text-anchor-navy/55 [text-shadow:0_1px_0_rgba(255,255,255,0.7)]">
                Anchor
              </span>
            </div>
            <PulseDot active={Boolean(account?.stream_connected)} />
          </div>

          <div className="mt-2.5 grid grid-cols-2 gap-1.5 sm:mt-3 sm:gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <DataBand label="Mode" value={account?.mode === 'paper' ? 'Paper' : 'Live'} />
            <DataBand label="Open positions" value={String(operatorState?.open_positions_count ?? 0)} />
            <DataBand label="Last sync" value={account?.last_reconciliation ? formatRelative(account.last_reconciliation, now.getTime()) : 'Waiting'} tone={statusTone} />
            <DataBand
              label="Next rebalance"
              value={operatorState?.next_window ? formatET(operatorState.next_window.starts_at, { hour: 'numeric', minute: '2-digit', hour12: true }) : '—'}
            />
          </div>
        </div>

        <div className="px-3 py-4 sm:px-5 sm:py-5 md:px-6">
          <h1 className="max-w-3xl font-display text-[2rem] leading-[1.02] text-anchor-navy sm:text-[3.2rem]">
            {heroTitle}
          </h1>

          <div className="mt-4 grid grid-cols-2 gap-2.5 sm:mt-5 sm:gap-3 lg:max-w-3xl">
            <HeroMetric
              label="Equity"
              value={formatCurrency(account?.equity ?? null)}
            />
            <HeroMetric
              label="Today"
              value={formatSignedCurrency(account?.today_pl ?? null)}
              valueClassName={(account?.today_pl ?? 0) !== 0 ? 'sm:text-[2.15rem]' : undefined}
              valueStyle={todayValueStyle(account?.today_pl ?? null)}
            />
            <HeroMetric
              label="Strategy"
              value={strategyLabel}
            />
          </div>
        </div>
      </header>

      <div className="grid gap-4 lg:grid-cols-[1.15fr_0.95fr]">
        <div className="space-y-4">
          <PremiumSection id="activity" title="Recent activity">
            <div className="space-y-2.5">
              {activityItems.length > 0 ? (
                activityItems.map((item) => (
                  <article
                    key={item.id}
                    style={{ borderLeftColor: toneAccent(item.tone), borderLeftWidth: '3px' }}
                    className="border border-anchor-navy/10 bg-anchor-parchment/30 p-4"
                  >
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
                      <div>
                        <p className="font-mono text-[9px] tracking-[0.18em] uppercase text-anchor-fog">{item.badge}</p>
                        <p className="mt-2 text-sm font-semibold leading-snug text-anchor-navy">{item.title}</p>
                        <p className="mt-1.5 text-sm leading-6 text-anchor-slate">{item.detail}</p>
                      </div>
                      <p className="font-mono text-[9px] tracking-[0.14em] uppercase text-anchor-fog sm:shrink-0">
                        {formatRelative(item.time, now.getTime())}
                      </p>
                    </div>
                  </article>
                ))
              ) : (
                <EmptyCard text="Nothing new yet." />
              )}
            </div>
          </PremiumSection>

          <PremiumSection
            id="open"
            title="Open positions"
            defaultOpen={positions.length > 0 || pendingOrders.length > 0}
          >
            <div className="space-y-5">
              <div>
                <p className="mb-3 font-mono text-[9px] tracking-[0.2em] uppercase text-anchor-fog">Current positions</p>
                <div className="space-y-2.5">
                  {positions.length > 0 ? positions.map((pos) => (
                    <div key={pos.id} className="border border-anchor-navy/15 bg-anchor-parchment/30 p-4">
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
                        <div>
                          <p className="text-base font-semibold text-anchor-navy">
                            {instrumentLabel(pos.instrument)}
                            <span className="ml-2 font-mono text-[9px] tracking-[0.14em] uppercase text-anchor-fog">
                              {directionText(pos.direction)}
                            </span>
                          </p>
                          <p className="mt-1 text-sm text-anchor-slate">
                            {formatUnits(pos.units)} contract{Math.abs(pos.units) === 1 ? '' : 's'} · entry {formatPrice(pos.avg_entry_price, 2)}
                          </p>
                        </div>
                        <p className={cn(
                          'font-mono text-lg font-semibold sm:shrink-0',
                          (pos.unrealized_pl ?? 0) > 0 ? 'text-anchor-win' : (pos.unrealized_pl ?? 0) < 0 ? 'text-anchor-loss' : 'text-anchor-navy',
                        )}>
                          {formatCurrency(pos.unrealized_pl)}
                        </p>
                      </div>
                    </div>
                  )) : <EmptyCard text="No positions are open right now." />}
                </div>
              </div>

              <div>
                <p className="mb-3 font-mono text-[9px] tracking-[0.2em] uppercase text-anchor-fog">Pending orders</p>
                <div className="space-y-2.5">
                  {pendingOrders.length > 0 ? pendingOrders.map((order) => (
                    <div key={order.id} className="border border-anchor-navy/15 bg-anchor-parchment/30 p-4">
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
                        <div>
                          <p className="text-base font-semibold text-anchor-navy">
                            {instrumentLabel(order.instrument)}
                            <span className="ml-2 font-mono text-[9px] tracking-[0.12em] uppercase text-anchor-fog">
                              {order.state.toLowerCase()}
                            </span>
                          </p>
                          <p className="mt-1 text-sm text-anchor-slate">
                            {order.order_type} · {directionText(order.direction)} · {formatUnits(order.units)} contract{Math.abs(order.units) === 1 ? '' : 's'}
                          </p>
                        </div>
                        <ArrowUpRight size={14} className="text-anchor-rule/75 sm:shrink-0" />
                      </div>
                    </div>
                  )) : <EmptyCard text="No orders are waiting in the market right now." />}
                </div>
              </div>
            </div>
          </PremiumSection>

          <PremiumSection id="results" title="Closed positions" defaultOpen={false}>
            {trades.length > 0 ? (
              <div>
                {snapshot?.history_notice && (
                  <div className="mb-4 border border-anchor-rule/60 bg-anchor-parchment/35 px-4 py-3">
                    <p className="font-mono text-[9px] tracking-[0.18em] uppercase text-anchor-fog">History note</p>
                    <p className="mt-2 text-sm leading-6 text-anchor-slate">{snapshot.history_notice}</p>
                  </div>
                )}
                {trades.slice(0, 6).map((trade) => {
                  const won = trade.net_pl > 0
                  return (
                    <div key={trade.id} className="border-b border-anchor-rule/40 py-3.5 last:border-0">
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
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
                          'font-mono text-lg font-semibold sm:shrink-0',
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
              <EmptyCard text="No closed positions yet." />
            )}
          </PremiumSection>
        </div>

        <div className="space-y-4">
          <PremiumSection id="watchlist" title="Target positions">
            {watchlist.length > 0 ? (
              <div>
                {watchlist.map((item) => (
                  <div key={item.instrument} className="border-b border-anchor-rule/40 py-3.5 last:border-0">
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
                      <div>
                        <p className="text-base font-semibold text-anchor-navy">{instrumentLabel(item.instrument)}</p>
                        <p className="mt-1 text-sm leading-5 text-anchor-slate">{item.note}</p>
                      </div>
                      <div className="flex flex-row flex-wrap items-center gap-1.5 sm:shrink-0 sm:flex-col sm:items-end">
                        <span className={cn('border px-2 py-0.5 font-mono text-[9px] tracking-[0.14em] uppercase', statusPillClass(item.status))}>
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
              <EmptyCard text="No target positions are ready right now." />
            )}
          </PremiumSection>

          <PremiumSection title="Things to watch" defaultOpen={false}>
            {upcomingEvents.length > 0 ? (
              <div>
                {upcomingEvents.map((event, index) => (
                  <div key={`${event.event_name}-${index}`} className="border-b border-anchor-rule/40 py-3.5 last:border-0">
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
                      <div>
                        <p className="text-sm font-semibold text-anchor-navy">{event.event_name}</p>
                        <p className="mt-1 font-mono text-[9px] tracking-[0.1em] uppercase text-anchor-fog">
                          {event.currency} · {event.impact} impact
                        </p>
                      </div>
                      <div className="flex flex-row items-center gap-2 sm:shrink-0 sm:flex-col sm:items-end sm:gap-1.5">
                        <CalendarClock size={13} className="text-anchor-rule/75" />
                        <p className="font-mono text-[9px] tracking-[0.1em] uppercase text-anchor-fog">
                          {formatEventTime(event.event_time)}
                        </p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyCard text="Nothing urgent is scheduled right now." />
            )}
          </PremiumSection>
        </div>
      </div>

      <footer className="mb-4 mt-8 flex items-center justify-between gap-4 border-t border-anchor-rule/40 px-1 pt-5">
        <p className="font-mono text-[9px] tracking-[0.18em] uppercase text-anchor-rule">
          Anchor · personal futures monitor
        </p>
        <p className="font-mono text-[9px] tracking-[0.14em] uppercase text-anchor-fog">
          {account?.stream_connected ? 'connected · synced' : 'disconnected'}
        </p>
      </footer>
    </div>
  )
}
