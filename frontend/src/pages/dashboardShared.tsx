import type { ReactNode } from 'react'
import { useHomepageSnapshot } from '../api/hooks'
import type { Direction, HomepageSnapshot, HomepageSnapshotResult } from '../types'

export const ET = 'America/New_York'

const MARKETS: Record<string, { name: string; desc: string }> = {
  MNQ: { name: 'Micro Nasdaq-100', desc: 'Tech stocks' },
  MGC: { name: 'Micro Gold', desc: 'Gold' },
  MCL: { name: 'Micro Crude Oil', desc: 'Crude oil' },
  ZN: { name: '10-Year Treasury', desc: 'US rates' },
  MES: { name: 'Micro S&P 500', desc: 'US equities' },
  MYM: { name: 'Micro Dow Jones', desc: 'Dow Jones 30' },
  M2K: { name: 'Micro Russell 2000', desc: 'Small caps' },
}

export function useDashboardSnapshot() {
  const { data } = useHomepageSnapshot()
  return data
}

export function marketInfo(instrument: string) {
  const root = instrument.replace(/-\d+$/, '').replace(/\d+$/, '')
  return MARKETS[root] ?? { name: instrument, desc: 'Futures contract' }
}

export function directionLabel(d: Direction) {
  return d === 'LONG' ? 'Long' : 'Short'
}

export function dollars(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—'
  return `${v < 0 ? '-' : ''}$${Math.abs(v).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

export function signedDollars(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—'
  return (v > 0 ? '+' : '') + dollars(v)
}

export function priceStr(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—'
  const d = v >= 100 ? 3 : 5
  return v.toFixed(d)
}

export function pctStr(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—'
  return `${v > 0 ? '+' : ''}${v.toFixed(1)}%`
}

export function titleCaseWord(value: string) {
  if (!value) return 'Unknown'
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function startOfDayInZone(value: Date, timeZone: string): Date {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(value)
  const year = parts.find((part) => part.type === 'year')?.value ?? '1970'
  const month = parts.find((part) => part.type === 'month')?.value ?? '01'
  const day = parts.find((part) => part.type === 'day')?.value ?? '01'
  return new Date(`${year}-${month}-${day}T00:00:00Z`)
}

export function humanTime(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const now = new Date(nowMs)
  const dStr = d.toLocaleDateString('en-US', { timeZone: ET })
  const tStr = d.toLocaleTimeString('en-US', { timeZone: ET, hour: 'numeric', minute: '2-digit', hour12: true })
  if (dStr === now.toLocaleDateString('en-US', { timeZone: ET })) return `Today ${tStr}`
  const y = new Date(now)
  y.setDate(y.getDate() - 1)
  if (dStr === y.toLocaleDateString('en-US', { timeZone: ET })) return `Yesterday ${tStr}`
  return `${d.toLocaleDateString('en-US', { timeZone: ET, month: 'short', day: 'numeric' })} · ${tStr}`
}

export function formatExplicitEtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString('en-US', {
    timeZone: ET,
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }) + ' ET'
}

export function formatScheduledTime(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return '—'
  const target = new Date(iso)
  const now = new Date(nowMs)
  const targetDay = startOfDayInZone(target, ET)
  const today = startOfDayInZone(now, ET)
  const dayDiff = Math.round((targetDay.getTime() - today.getTime()) / 86_400_000)
  const absolute = target.toLocaleString('en-US', {
    timeZone: ET,
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  })
  if (dayDiff === 0) return `Today, ${absolute} ET`
  if (dayDiff === 1) return `Tomorrow, ${absolute} ET`
  return `${absolute} ET`
}

export function holdTime(openedAt: string, endMs = Date.now()): string {
  const ms = Math.max(0, endMs - new Date(openedAt).getTime())
  const mins = Math.floor(ms / 60000)
  const days = Math.floor(mins / 1440)
  const hrs = Math.floor((mins % 1440) / 60)
  const m = mins % 60
  if (days > 0) return `${days}d ${hrs}h`
  if (hrs > 0) return `${hrs}h ${m} min`
  return `${m} min`
}

export function cn(...cls: Array<string | false | null | undefined>) {
  return cls.filter(Boolean).join(' ')
}

export function operatorState(
  snapshot: HomepageSnapshot | undefined,
  marketDataLabel: string,
  nowMs: number,
  wsConnected: boolean,
) {
  const account = snapshot?.account
  const status = snapshot?.status
  const nextRebalance = snapshot?.operator?.next_window?.starts_at
  const degraded = snapshot?.operator?.operator_state === 'DEGRADED'
  const running = wsConnected && !degraded
  const operatorLabel = running ? 'Healthy' : degraded ? 'Needs attention' : 'Offline'
  const syncText = humanTime(status?.last_broker_sync ?? account?.last_reconciliation, nowMs)
  const rebalanceText = formatScheduledTime(nextRebalance, nowMs)
  return {
    running,
    degraded,
    operatorLabel,
    syncText,
    rebalanceText,
    summary: `Broker synced ${syncText}. ${status?.drawdown_guard?.active ? 'Guardrail active.' : 'No active guardrails.'} Next rebalance ${rebalanceText}. ${marketDataLabel} feed.`,
  }
}

export function SectionCard({
  title,
  kicker,
  children,
}: {
  title: string
  kicker?: string
  children: ReactNode
}) {
  return (
    <section className="panel-glass mb-5 overflow-hidden rounded-[24px] border border-white/8 shadow-[0_16px_42px_rgba(0,0,0,0.24)]">
      <div className="border-b border-white/8 px-5 py-4 sm:px-6">
        {kicker && <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">{kicker}</p>}
        <h2 className="font-display text-[1.25rem] leading-tight text-anchor-brass sm:text-[1.4rem]">{title}</h2>
      </div>
      {children}
    </section>
  )
}

export function TradeHistoryRow({ trade, nowMs }: { trade: HomepageSnapshotResult; nowMs: number }) {
  const mkt = marketInfo(trade.instrument)
  const won = trade.net_pl > 0
  const closeMs = new Date(trade.closed_at).getTime()

  return (
    <div className="flex items-start justify-between gap-3 border-b border-white/7 py-4 last:border-0">
      <div>
        <p className="text-[15px] font-semibold text-anchor-navy/95">{mkt.name}</p>
        <p className="mt-1 text-[13px] text-anchor-slate/95">
          {trade.direction === 'LONG' ? 'Long' : 'Short'} · held {holdTime(trade.opened_at, closeMs)}
        </p>
        <p className="mt-1 font-mono text-[11px] tracking-[0.05em] text-anchor-fog/88">
          Closed {humanTime(trade.closed_at, nowMs)}
        </p>
      </div>
      <p className={cn('font-mono text-[17px] font-bold tracking-tight', won ? 'text-anchor-win' : 'text-anchor-loss')}>
        {signedDollars(trade.net_pl)}
      </p>
    </div>
  )
}
