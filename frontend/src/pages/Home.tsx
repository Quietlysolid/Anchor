import { useEffect, useState } from 'react'
import { useHomepageSnapshot } from '../api/hooks'
import { EquityCurve } from '../components/charts/EquityCurve'
import { usePositionStore, useSystemStore } from '../store'
import type { Direction, HomepageSnapshotResult } from '../types'

// ── Market plain-English names ────────────────────────────────────────────────

const MARKETS: Record<string, { name: string; desc: string }> = {
  MNQ: { name: 'Micro Nasdaq-100',  desc: 'Tech stocks — Apple, Google, Amazon' },
  MGC: { name: 'Micro Gold',        desc: 'Gold commodity' },
  MCL: { name: 'Micro Crude Oil',   desc: 'Oil commodity' },
  ZN:  { name: '10-Year Treasury',  desc: 'US government bonds' },
  MES: { name: 'Micro S&P 500',     desc: 'Broad US stock market' },
  MYM: { name: 'Micro Dow Jones',   desc: 'Dow Jones 30 stocks' },
  M2K: { name: 'Micro Russell 2000',desc: 'Small-cap US stocks' },
}

function marketInfo(instrument: string) {
  const root = instrument.replace(/-\d+$/, '').replace(/\d+$/, '')
  return MARKETS[root] ?? { name: instrument, desc: 'Futures contract' }
}

function directionLabel(d: Direction) {
  return d === 'LONG' ? 'Long' : 'Short'
}

// ── Formatters ────────────────────────────────────────────────────────────────

function dollars(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—'
  return `${v < 0 ? '-' : ''}$${Math.abs(v).toLocaleString('en-US', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`
}

function signedDollars(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—'
  return (v > 0 ? '+' : '') + dollars(v)
}

function priceStr(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—'
  const d = v >= 10000 ? 2 : v >= 1000 ? 2 : v >= 100 ? 3 : 5
  return v.toFixed(d)
}

function moveStr(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—'
  const abs = Math.abs(v)
  const d = abs >= 10000 ? 2 : abs >= 1000 ? 2 : abs >= 100 ? 3 : 5
  return abs.toFixed(d)
}

const ET = 'America/New_York'

function humanTime(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const now = new Date(nowMs)
  const dStr  = d.toLocaleDateString('en-US', { timeZone: ET })
  const tStr  = d.toLocaleTimeString('en-US', { timeZone: ET, hour: 'numeric', minute: '2-digit', hour12: true })
  if (dStr === now.toLocaleDateString('en-US', { timeZone: ET })) return `Today ${tStr}`
  const y = new Date(now); y.setDate(y.getDate() - 1)
  if (dStr === y.toLocaleDateString('en-US', { timeZone: ET })) return `Yesterday ${tStr}`
  return d.toLocaleDateString('en-US', { timeZone: ET, month: 'short', day: 'numeric' }) + ' · ' + tStr
}

function etFmt(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-US', { timeZone: ET, ...opts })
}

function holdTime(openedAt: string, endMs = Date.now()): string {
  const ms   = Math.max(0, endMs - new Date(openedAt).getTime())
  const mins = Math.floor(ms / 60000)
  const days = Math.floor(mins / 1440)
  const hrs  = Math.floor((mins % 1440) / 60)
  const m    = mins % 60
  if (days > 0) return `${days}d ${hrs}h`
  if (hrs  > 0) return `${hrs}h ${m} min`
  return `${m} min`
}

function cn(...cls: Array<string | false | null | undefined>) {
  return cls.filter(Boolean).join(' ')
}

function titleCaseWord(value: string): string {
  if (!value) return 'Unknown'
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function pctStr(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—'
  return `${v > 0 ? '+' : ''}${v.toFixed(1)}%`
}

// ── SVG mark ─────────────────────────────────────────────────────────────────

function AnchorMark({ size = 16, className = '' }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" className={className} aria-hidden>
      <circle cx="10" cy="4"  r="2.2"  stroke="currentColor" strokeWidth="1.5" />
      <line x1="10" y1="6.2" x2="10" y2="17"  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <line x1="5"  y1="8.8" x2="15" y2="8.8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M10 17 Q 5.5 17.5 4.5 14"  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none" />
      <path d="M10 17 Q 14.5 17.5 15.5 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none" />
    </svg>
  )
}

// ── Trade history row ─────────────────────────────────────────────────────────

function TradeRow({ trade, nowMs }: { trade: HomepageSnapshotResult; nowMs: number }) {
  const mkt    = marketInfo(trade.instrument)
  const won    = trade.net_pl > 0
  const closeMs = new Date(trade.closed_at).getTime()

  return (
    <div className="flex items-start justify-between gap-3 border-b border-white/7 py-4.5 last:border-0">
      <div>
        <p className="text-[15px] font-semibold text-anchor-navy/95">{mkt.name}</p>
        <p className="mt-1 text-[13px] text-anchor-slate/90">
          {trade.direction === 'LONG' ? 'Long' : 'Short'}
          {'  ·  '}held {holdTime(trade.opened_at, closeMs)}
        </p>
        <p className="mt-1 font-mono text-[10px] tracking-[0.06em] text-anchor-fog/75">
          Closed {humanTime(trade.closed_at, nowMs)}
        </p>
      </div>
      <div className="shrink-0 text-right">
        <p className={cn('font-mono text-[17px] font-bold tracking-tight', won ? 'text-anchor-win' : 'text-anchor-loss')}>
          {signedDollars(trade.net_pl)}
        </p>
      </div>
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function Home() {
  const [nowMs, setNowMs] = useState(() => Date.now())

  // Real-time data from WebSocket store — updates every ~7 seconds
  const { equity: wsEquity, wsConnected } = useSystemStore()
  const { positions }                     = usePositionStore()

  // Snapshot data — refreshes every 15 seconds
  const { data: snapshot } = useHomepageSnapshot()

  useEffect(() => {
    document.title = 'Anchor'
    const t = setInterval(() => setNowMs(Date.now()), 60_000)
    return () => clearInterval(t)
  }, [])

  const account       = snapshot?.account
  const status        = snapshot?.status
  const mode          = account?.mode ?? 'paper'
  const equity        = wsEquity > 0 ? wsEquity : (account?.equity ?? 0)
  const cash          = account?.balance ?? 0
  const brokerDayPL   = account?.broker_day_pl ?? account?.today_pl ?? null
  const anchorDayPL   = account?.anchor_day_pl ?? null
  const accountId     = account?.account_id ?? 'unknown'
  const marketDataMode = account?.market_data_mode ?? 'unknown'
  const marketDataLabel = titleCaseWord(marketDataMode)
  const nextRebalance = snapshot?.operator?.next_window?.starts_at
  const recentTrades  = snapshot?.recent_results ?? []
  const performance   = snapshot?.performance
  const readiness     = snapshot?.readiness
  const drawdownGuard = status?.drawdown_guard
  const execution     = snapshot?.execution
  const decisions     = snapshot?.decisions ?? []
  const alerts        = snapshot?.alerts ?? []
  const degraded      = snapshot?.operator?.operator_state === 'DEGRADED'
  const running       = wsConnected && !degraded

  // Total unrealized P&L across all open positions
  const totalPaperPL = positions.reduce((s, p) => s + (p.unrealized_pl ?? 0), 0)

  const readinessTone = readiness?.state === 'candidate_for_live_mirror'
    ? 'text-anchor-win'
    : readiness?.state === 'paper_validated'
      ? 'text-anchor-brass'
      : readiness?.state === 'observe'
        ? 'text-anchor-warn'
        : 'text-anchor-loss'

  return (
    <div className="animate-anchor-fade-up mx-auto max-w-2xl px-4 pb-20 pt-6 sm:px-6">

      {/* ── Status bar ─────────────────────────────────────────────── */}
      <div className={cn(
        'mb-7 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[22px] border px-4 py-3.5 shadow-[0_10px_30px_rgba(0,0,0,0.16)] backdrop-blur-sm',
        running
          ? 'border-white/8 bg-white/[0.045]'
          : 'border-white/8 bg-white/[0.045]',
      )}>
        {/* Live dot */}
        <div className="flex items-center gap-2 shrink-0">
          <span className="relative inline-flex h-2.5 w-2.5">
            {running && (
              <span className="absolute h-full w-full animate-ping rounded-full bg-anchor-win/50 opacity-75" />
            )}
            <span className={cn(
              'relative inline-flex h-2.5 w-2.5 rounded-full',
              running ? 'bg-anchor-win' : 'bg-anchor-loss',
            )} />
          </span>
          <span className={cn(
            'font-mono text-[11px] font-bold uppercase tracking-widest',
            running ? 'text-anchor-win' : 'text-anchor-loss',
          )}>
            {running ? 'Running' : degraded ? 'Needs attention' : 'Offline'}
          </span>
        </div>

        <span className="text-anchor-border">·</span>

        <span className="font-mono text-[11px] tracking-wider text-anchor-fog">
          {mode === 'paper' ? 'Paper money' : 'Live account'}
        </span>

        <span className="text-anchor-border">·</span>

        <span className="font-mono text-[11px] tracking-wider text-anchor-fog">
          {accountId}
        </span>

        <span className="text-anchor-border">·</span>

        <span className="font-mono text-[11px] tracking-wider text-anchor-fog">
          {marketDataLabel} feed
        </span>

        {/* Equity + today P&L pushed to the right */}
        <div className="flex w-full flex-col gap-1 sm:ml-auto sm:w-auto sm:items-end">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="font-mono text-[11px] font-bold text-anchor-navy">
              Net liq {dollars(equity)}
            </span>
            {brokerDayPL != null && (
              <span className={cn(
                'font-mono text-[11px] font-semibold',
                brokerDayPL > 0 ? 'text-anchor-win'
                                : brokerDayPL < 0 ? 'text-anchor-loss'
                            : 'text-anchor-fog',
              )}>
                {signedDollars(brokerDayPL)} broker day
              </span>
            )}
          </div>
          {anchorDayPL != null && (
            <span className="font-mono text-[10px] text-anchor-fog/72">
              Bot analytics: {signedDollars(anchorDayPL)} since midnight ET
            </span>
          )}
        </div>
      </div>

      {/* ── Alerts ──────────────────────────────────────────────── */}
      <section className="panel-glass animate-anchor-fade-up mb-6 overflow-hidden rounded-[24px] border border-white/8 shadow-[0_16px_42px_rgba(0,0,0,0.22)]">
        <div className="border-b border-white/8 px-5 py-4 sm:px-6">
          <h2 className="font-display text-[1.3rem] leading-tight text-anchor-brass sm:text-[1.45rem]">
            Alerts
          </h2>
          <p className="mt-0.5 text-xs text-anchor-fog">
            What needs attention right now, without digging through logs
          </p>
        </div>
        {alerts.length > 0 ? (
          <div className="divide-y divide-white/7">
            {alerts.map((alert, idx) => (
              <div key={`${alert.title}-${idx}`} className="flex gap-3 px-5 py-4 sm:px-6">
                <div className={cn(
                  'mt-1.5 h-2 w-2 shrink-0 rounded-full',
                  alert.severity === 'critical' ? 'bg-anchor-loss' :
                  alert.severity === 'warning' ? 'bg-anchor-warn' :
                  'bg-anchor-rule/60',
                )} />
                <div>
                  <p className="text-[13px] font-semibold text-anchor-navy">{alert.title}</p>
                  <p className="mt-1 text-[11px] text-anchor-fog/82">{alert.detail}</p>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="px-5 py-6 sm:px-6">
            <p className="text-sm text-anchor-win">No active alerts.</p>
            <p className="mt-1 text-xs text-anchor-fog/60">
              Broker sync, feed mode, guardrails, and readiness checks are all clear right now.
            </p>
          </div>
        )}
      </section>

      {/* ── System status ─────────────────────────────────────────── */}
      <section className="panel-glass animate-anchor-fade-up mb-6 overflow-hidden rounded-[24px] border border-white/8 shadow-[0_16px_42px_rgba(0,0,0,0.24)]">
        <div className="border-b border-white/8 px-5 py-4 sm:px-6">
          <h2 className="font-display text-[1.3rem] leading-tight text-anchor-brass sm:text-[1.45rem]">
            System status
          </h2>
          <p className="mt-0.5 text-xs text-anchor-fog">
            Automation health, broker sync, and the latest control actions
          </p>
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-5 px-5 py-5 sm:grid-cols-4 sm:px-6">
          <div>
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Last broker sync</p>
            <p className="mt-1 text-[13px] font-semibold text-anchor-navy">
              {humanTime(status?.last_broker_sync ?? account?.last_reconciliation, nowMs)}
            </p>
          </div>
          <div>
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Next rebalance</p>
            <p className="mt-1 text-[13px] font-semibold text-anchor-navy">
              {nextRebalance
                ? etFmt(nextRebalance, { weekday: 'short', hour: 'numeric', minute: '2-digit', hour12: true }) + ' ET'
                : '—'}
            </p>
          </div>
          <div>
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Open positions</p>
            <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">
              {status?.open_positions_count ?? positions.length}
            </p>
          </div>
          <div>
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Open orders</p>
            <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">
              {status?.open_orders_count ?? snapshot?.operator?.working_orders_count ?? 0}
            </p>
          </div>
        </div>
        <div className="border-t border-white/8 px-5 py-5 sm:px-6">
          <div className="grid gap-5 sm:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
            <div>
              <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Last rebalance</p>
              {status?.last_rebalance ? (
                <>
                  <p className="mt-1 text-[13px] font-semibold text-anchor-navy">{status.last_rebalance.title}</p>
                  <p className="mt-1 text-[11px] text-anchor-fog/82">{status.last_rebalance.detail}</p>
                  <p className="mt-1 font-mono text-[9px] tracking-[0.06em] text-anchor-fog/65">
                    {humanTime(status.last_rebalance.occurred_at, nowMs)}
                  </p>
                </>
              ) : (
                <>
                  <p className="mt-1 text-[13px] font-semibold text-anchor-navy">No rebalance yet</p>
                  <p className="mt-1 text-[11px] text-anchor-fog/82">
                    The first rebalance summary will appear here once the engine records it.
                  </p>
                </>
              )}
            </div>
            <div>
              <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Active guardrails</p>
              {drawdownGuard?.active && (
                <div className="mt-1 mb-3 rounded-[18px] border border-amber-500/20 bg-amber-500/8 px-3.5 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-[12px] font-semibold text-anchor-navy">{drawdownGuard.title}</p>
                      <p className="mt-0.5 text-[11px] text-anchor-fog/82">{drawdownGuard.reason}</p>
                    </div>
                    <span className={cn(
                      'shrink-0 font-mono text-[10px] font-semibold uppercase tracking-[0.16em]',
                      drawdownGuard.mode === 'reduced' ? 'text-anchor-warn' : 'text-anchor-loss',
                    )}>
                      {drawdownGuard.mode === 'reduced' ? 'Reduced' : 'Blocking'}
                    </span>
                  </div>
                  <p className="mt-2 text-[11px] text-anchor-fog/84">{drawdownGuard.blocking}</p>
                  <p className="mt-1 text-[11px] text-anchor-fog/72">Clears when: {drawdownGuard.clear_when}</p>
                  <p className="mt-2 font-mono text-[10px] text-anchor-fog/72">
                    Drawdown {pctStr(drawdownGuard.current_drawdown_pct)} · reduce at {pctStr(drawdownGuard.reduce_threshold_pct)} · halt at {pctStr(drawdownGuard.halt_threshold_pct)}
                  </p>
                  {drawdownGuard.occurred_at && (
                    <p className="mt-1 font-mono text-[9px] tracking-[0.06em] text-anchor-fog/65">
                      Triggered {humanTime(drawdownGuard.occurred_at, nowMs)}
                    </p>
                  )}
                </div>
              )}
              {status?.active_guardrails?.length ? (
                <div className="mt-1 space-y-2">
                  {status.active_guardrails.map((guardrail) => (
                    <div key={`${guardrail.event_type}-${guardrail.occurred_at}`} className="rounded-[16px] border border-white/7 bg-white/[0.03] px-3 py-2.5">
                      <p className="text-[12px] font-semibold text-anchor-navy">{guardrail.title}</p>
                      <p className="mt-0.5 text-[11px] text-anchor-fog/82">{guardrail.detail}</p>
                      <p className="mt-1 font-mono text-[9px] tracking-[0.06em] text-anchor-fog/65">
                        {humanTime(guardrail.occurred_at, nowMs)}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <>
                  <p className="mt-1 text-[13px] font-semibold text-anchor-win">No active guardrails</p>
                  <p className="mt-1 text-[11px] text-anchor-fog/82">
                    No recent margin or drawdown protection events are active.
                  </p>
                </>
              )}
            </div>
          </div>
        </div>
      </section>

      {/* ── Broker account ─────────────────────────────────────────── */}
      <section className="panel-glass animate-anchor-fade-up mb-6 overflow-hidden rounded-[24px] border border-white/8 shadow-[0_16px_42px_rgba(0,0,0,0.26)]">
        <div className="border-b border-white/8 px-5 py-4 sm:px-6">
          <h2 className="font-display text-[1.3rem] leading-tight text-anchor-brass sm:text-[1.45rem]">
            Broker account
          </h2>
          <p className="mt-0.5 text-xs text-anchor-fog">
            Live IBKR account state and margin health
          </p>
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-5 px-5 py-5 sm:grid-cols-4 sm:px-6">
          <div>
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Net liq</p>
            <p className="mt-1 font-mono text-[1.15rem] font-bold text-anchor-navy">{dollars(equity)}</p>
          </div>
          <div>
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Broker day</p>
            <p className={cn('mt-1 font-mono text-[1.15rem] font-bold',
              (brokerDayPL ?? 0) > 0 ? 'text-anchor-win' : (brokerDayPL ?? 0) < 0 ? 'text-anchor-loss' : 'text-anchor-navy',
            )}>
              {signedDollars(brokerDayPL)}
            </p>
          </div>
          <div>
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Cash</p>
            <p className="mt-1 font-mono text-[1.15rem] font-bold text-anchor-navy">{dollars(cash)}</p>
          </div>
          <div>
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Margin usage</p>
            <p className="mt-1 font-mono text-[1.15rem] font-bold text-anchor-navy">{pctStr(account?.margin_usage_pct)}</p>
          </div>
          <div>
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Available funds</p>
            <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{dollars(account?.available_funds)}</p>
          </div>
          <div>
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Excess liquidity</p>
            <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{dollars(account?.excess_liquidity)}</p>
          </div>
          <div>
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Initial margin</p>
            <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{dollars(account?.initial_margin)}</p>
          </div>
          <div>
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Maint. margin</p>
            <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{dollars(account?.maintenance_margin)}</p>
          </div>
        </div>
      </section>

      {/* ── Execution ────────────────────────────────────────────── */}
      <section className="panel-glass animate-anchor-fade-up mb-6 overflow-hidden rounded-[24px] border border-white/8 shadow-[0_16px_42px_rgba(0,0,0,0.24)]">
        <div className="border-b border-white/8 px-5 py-4 sm:px-6">
          <h2 className="font-display text-[1.3rem] leading-tight text-anchor-brass sm:text-[1.45rem]">
            Execution
          </h2>
          <p className="mt-0.5 text-xs text-anchor-fog">
            Open broker orders and the most recent fills recorded by Anchor
          </p>
        </div>
        <div className="grid gap-0 sm:grid-cols-2">
          <div className="border-b border-white/8 sm:border-b-0 sm:border-r sm:border-white/8">
            <div className="px-5 py-4 sm:px-6">
              <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Open orders</p>
            </div>
            {(execution?.open_orders?.length ?? 0) > 0 ? (
              <div className="divide-y divide-white/7">
                {execution?.open_orders.map((order) => (
                  <div key={order.id} className="px-5 py-4 sm:px-6">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-[13px] font-semibold text-anchor-navy">{order.instrument}</p>
                        <p className="mt-0.5 text-[11px] text-anchor-fog/80">
                          {directionLabel(order.direction)} · {order.order_type} · {Math.abs(order.units)} contract{Math.abs(order.units) !== 1 ? 's' : ''}
                        </p>
                      </div>
                      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-anchor-warn">
                        {order.state}
                      </span>
                    </div>
                    <p className="mt-1 font-mono text-[9px] tracking-[0.06em] text-anchor-fog/65">
                      {humanTime(order.created_at, nowMs)}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <div className="px-5 py-6 sm:px-6">
                <p className="text-sm text-anchor-fog">No open broker orders.</p>
                <p className="mt-1 text-xs text-anchor-fog/60">
                  Working orders will appear here until they fill, cancel, or expire.
                </p>
              </div>
            )}
          </div>
          <div>
            <div className="px-5 py-4 sm:px-6">
              <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Recent fills</p>
            </div>
            {(execution?.recent_fills?.length ?? 0) > 0 ? (
              <div className="divide-y divide-white/7">
                {execution?.recent_fills.map((fill) => (
                  <div key={fill.id} className="px-5 py-4 sm:px-6">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-[13px] font-semibold text-anchor-navy">{fill.instrument}</p>
                        <p className="mt-0.5 text-[11px] text-anchor-fog/80">
                          {directionLabel(fill.direction)} · {fill.order_type} · {Math.abs(fill.units)} contract{Math.abs(fill.units) !== 1 ? 's' : ''}
                        </p>
                        <p className="mt-1 text-[11px] text-anchor-fog/82">
                          Fill <span className="font-mono text-anchor-navy">{priceStr(fill.fill_price)}</span>
                          {fill.commission ? ` · Comm ${dollars(fill.commission)}` : ''}
                          {fill.slippage_pips != null ? ` · Slip ${fill.slippage_pips.toFixed(2)}` : ''}
                        </p>
                      </div>
                    </div>
                    <p className="mt-1 font-mono text-[9px] tracking-[0.06em] text-anchor-fog/65">
                      {humanTime(fill.fill_at, nowMs)}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <div className="px-5 py-6 sm:px-6">
                <p className="text-sm text-anchor-fog">No recent fills yet.</p>
                <p className="mt-1 text-xs text-anchor-fog/60">
                  Filled orders will appear here once execution audit records are written.
                </p>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ── Performance ───────────────────────────────────────────── */}
      <section className="panel-glass animate-anchor-fade-up mb-6 overflow-hidden rounded-[24px] border border-white/8 shadow-[0_16px_42px_rgba(0,0,0,0.24)]">
        <div className="border-b border-white/8 px-5 py-4 sm:px-6">
          <h2 className="font-display text-[1.3rem] leading-tight text-anchor-brass sm:text-[1.45rem]">
            Strategy performance
          </h2>
          <p className="mt-0.5 text-xs text-anchor-fog">
            Account growth, drawdown, and realized performance over time
          </p>
        </div>
        <div className="px-5 py-5 sm:px-6">
          <EquityCurve data={performance?.equity_curve ?? []} />
          <div className="mt-5 grid grid-cols-2 gap-x-4 gap-y-5 sm:grid-cols-4">
            <div>
              <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">1W return</p>
              <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{pctStr(performance?.returns['1w_pct'])}</p>
            </div>
            <div>
              <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">MTD return</p>
              <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{pctStr(performance?.returns.mtd_pct)}</p>
            </div>
            <div>
              <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Since start</p>
              <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{pctStr(performance?.returns.since_start_pct)}</p>
            </div>
            <div>
              <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Track record</p>
              <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{performance?.track_record_days ?? 0} days</p>
            </div>
            <div>
              <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Current drawdown</p>
              <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{pctStr(performance?.drawdown.current_pct)}</p>
            </div>
            <div>
              <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Max drawdown</p>
              <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{pctStr(performance?.drawdown.max_pct)}</p>
            </div>
            <div>
              <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Realized MTD</p>
              <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{signedDollars(performance?.realized_pl.mtd)}</p>
            </div>
            <div>
              <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Realized total</p>
              <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{signedDollars(performance?.realized_pl.since_start)}</p>
            </div>
          </div>
        </div>
      </section>

      {/* ── Readiness ─────────────────────────────────────────────── */}
      <section className="panel-glass animate-anchor-fade-up mb-6 overflow-hidden rounded-[24px] border border-white/8 shadow-[0_16px_42px_rgba(0,0,0,0.22)]">
        <div className="border-b border-white/8 px-5 py-4 sm:px-6">
          <h2 className="font-display text-[1.3rem] leading-tight text-anchor-brass sm:text-[1.45rem]">
            Readiness
          </h2>
          <p className="mt-0.5 text-xs text-anchor-fog">
            Paper-track confidence for scaling toward live mirroring
          </p>
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-5 px-5 py-5 sm:grid-cols-4 sm:px-6">
          <div className="col-span-2 sm:col-span-1">
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">State</p>
            <p className={cn('mt-1 font-mono text-[1.1rem] font-bold', readinessTone)}>{readiness?.label ?? 'Observe'}</p>
            <p className="mt-1 text-[11px] text-anchor-fog/78">
              {readiness?.passed_checks ?? 0}/{readiness?.total_checks ?? 0} checks passed
            </p>
          </div>
          <div>
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Rebalances</p>
            <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{readiness?.rebalance_count ?? 0}</p>
          </div>
          <div>
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Margin incidents</p>
            <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{readiness?.margin_incidents ?? 0}</p>
          </div>
          <div>
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Ops incidents</p>
            <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{readiness?.operational_incidents ?? 0}</p>
          </div>
        </div>
        <div className="border-t border-white/8 px-5 py-4 sm:px-6">
          <p className="text-[12px] text-anchor-fog/82">
            {readiness?.recommendation ?? 'Paper trading is still being evaluated.'}
          </p>
          {readiness?.incident_cutoff && (
            <p className="mt-2 text-[11px] text-anchor-fog/70">
              Incident checks currently count from {humanTime(readiness.incident_cutoff, nowMs)} onward.
            </p>
          )}
          {(readiness?.failing_checks?.length ?? 0) > 0 && (
            <p className="mt-2 text-[11px] text-anchor-fog/72">
              Holding live mirroring on: {readiness?.failing_checks.join(', ')}
            </p>
          )}
        </div>
        <div className="border-t border-white/8 px-5 py-5 sm:px-6">
          <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/65">Live-mirroring criteria</p>
          <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
            {(readiness?.criteria ?? []).map((criterion) => (
              <div key={criterion.key} className="rounded-[16px] border border-white/7 bg-white/[0.03] px-3 py-2.5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-[12px] font-semibold text-anchor-navy">{criterion.label}</p>
                    <p className="mt-0.5 text-[11px] text-anchor-fog/80">
                      {criterion.value} now · target {criterion.target}
                    </p>
                  </div>
                  <span className={cn(
                    'font-mono text-[10px] font-semibold uppercase tracking-[0.16em]',
                    criterion.passed ? 'text-anchor-win' : 'text-anchor-loss',
                  )}>
                    {criterion.passed ? 'Pass' : 'Hold'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Portfolio ──────────────────────────────────────────────── */}
      <section className="panel-glass animate-anchor-fade-up mb-7 overflow-hidden rounded-[28px] border border-white/8 shadow-[0_20px_60px_rgba(0,0,0,0.38)]">

        {/* Card header */}
        <div className="flex flex-col gap-4 border-b border-white/8 px-5 py-5 sm:flex-row sm:items-start sm:justify-between sm:px-6">
          <div>
            <h2 className="font-display text-[1.5rem] leading-none text-anchor-brass sm:text-[1.7rem]">
              Broker positions
            </h2>
            {positions.length > 0 && totalPaperPL !== 0 && (
              <p className={cn(
                'mt-2 text-sm leading-snug',
                totalPaperPL > 0 ? 'text-anchor-win' : 'text-anchor-loss',
              )}>
                {signedDollars(totalPaperPL)} total unrealized
              </p>
            )}
          </div>
          <div className="shrink-0 text-left sm:text-right">
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-anchor-fog/70">
              Net liquidation
            </p>
            <p className="mt-1 font-mono text-[2rem] font-bold tracking-tight text-anchor-navy sm:text-[2.35rem]">
              {dollars(equity)}
            </p>
            <p className="mt-1 font-mono text-[10px] uppercase tracking-[0.18em] text-anchor-fog/70">
              Cash {dollars(cash)}
            </p>
          </div>
        </div>

        {/* Open positions — each one is its own row */}
        {positions.length > 0 ? (
          <>
            <div className="divide-y divide-white/8">
              {positions.map((pos) => {
                const mkt  = marketInfo(pos.instrument)
                const pl   = pos.unrealized_pl ?? 0
                const plUp = pl > 0
                const plDn = pl < 0

                const exitHint = pos.stop_loss != null
                  ? pos.direction === 'LONG'
                    ? `Exit protection: sell if price drops to ${priceStr(pos.stop_loss)}`
                    : `Exit protection: buy back if price rises to ${priceStr(pos.stop_loss)}`
                  : null

                const priceMove = pos.current_price != null
                  ? pos.direction === 'LONG'
                    ? pos.current_price - pos.avg_entry_price
                    : pos.avg_entry_price - pos.current_price
                  : null

                const plExplainer = pos.current_price != null
                  ? `${priceMove != null && priceMove >= 0 ? '+' : '-'}${moveStr(priceMove)}`
                  : null

                return (
                  <div key={pos.id} className="px-5 py-5 sm:px-6">
                    {/* Name + live P&L */}
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-5">
                      <div>
                        <p className="text-[1.05rem] font-semibold leading-snug text-anchor-navy">{mkt.name}</p>
                        <p className="mt-1 text-[11px] tracking-[0.08em] uppercase text-anchor-fog/80">{mkt.desc}</p>
                        <p className="mt-1 font-mono text-[10px] tracking-[0.06em] text-anchor-fog/70">Broker contract {pos.instrument}</p>
                      </div>
                      <div className="shrink-0 text-left sm:min-w-[132px] sm:text-right">
                        <p className={cn(
                          'font-mono text-[2rem] font-bold leading-none tracking-tight',
                          plUp ? 'text-anchor-win' : plDn ? 'text-anchor-loss' : 'text-anchor-navy/40',
                        )}>
                          {signedDollars(pl)}
                        </p>
                        <p className="mt-1 font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/62">Broker unrealized</p>
                        {plExplainer && (
                          <p className="mt-1 font-mono text-[11px] text-anchor-fog/85">{plExplainer}</p>
                        )}
                      </div>
                    </div>

                    {/* Position details */}
                    <div className="mt-3.5 space-y-1.5">
                      <p className="text-[12px] text-anchor-slate/78">
                        <span className={cn(
                          'font-semibold',
                          pos.direction === 'LONG' ? 'text-anchor-win' : 'text-anchor-loss',
                        )}>
                          {directionLabel(pos.direction)}
                        </span>
                        {'  ·  '}{Math.abs(pos.units)} contract{Math.abs(pos.units) !== 1 ? 's' : ''}
                        {'  ·  '}{holdTime(pos.opened_at, nowMs)}
                      </p>

                      {pos.current_price != null && (
                        <p className="text-[12px] text-anchor-slate/82">
                          Entry{' '}
                          <span className="font-mono font-semibold text-anchor-navy">
                            {priceStr(pos.avg_entry_price)}
                          </span>
                          {'  ·  '}Now{' '}
                          <span className="font-mono font-semibold text-anchor-navy">
                            {priceStr(pos.current_price)}
                          </span>
                        </p>
                      )}


                      {exitHint && (
                        <p className="pt-1 pr-1 text-[11px] leading-relaxed tracking-[0.02em] text-anchor-fog/72">{exitHint}</p>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>

          </>
        ) : (
          <div className="px-5 py-10 text-center sm:px-6">
            <p className="text-sm text-anchor-fog">No positions open right now.</p>
            <p className="mt-1.5 text-xs text-anchor-fog/60">
              Anchor checks for signals every day. Trades will appear here when a position is open.
            </p>
          </div>
        )}

        {/* Next rebalance hint */}
        {nextRebalance && (
          <div className="border-t border-white/8 bg-white/[0.02] px-5 py-3.5 sm:px-6">
            <p className="text-xs text-anchor-fog">
              Next rebalance:{' '}
              <span className="font-semibold text-anchor-navy">
                {etFmt(nextRebalance, { weekday: 'short', hour: 'numeric', minute: '2-digit', hour12: true })} ET
              </span>
                          </p>
          </div>
        )}
      </section>

      {/* ── Recent decisions ─────────────────────────────────────────── */}
      <section className="panel-glass animate-anchor-fade-up-delayed mb-6 overflow-hidden rounded-[24px] border border-white/7 shadow-[0_14px_36px_rgba(0,0,0,0.26)]">
        <div className="border-b border-white/7 px-5 py-4 sm:px-6">
          <h2 className="font-display text-[1.3rem] leading-tight text-anchor-brass sm:text-[1.45rem]">
            Recent decisions
          </h2>
          <p className="mt-0.5 text-xs text-anchor-fog">
            Rebalances, warnings, and the most important recent bot actions
          </p>
        </div>

        {decisions.length > 0 ? (
          <div className="divide-y divide-white/7">
            {decisions.map((item) => (
              <div key={item.id} className="flex gap-3 px-5 py-4 sm:px-6">
                <div className={cn(
                  'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full',
                  item.tone === 'good' ? 'bg-anchor-win' :
                  item.tone === 'bad'  ? 'bg-anchor-loss' :
                  item.tone === 'warn' ? 'bg-anchor-warn' :
                  'bg-anchor-rule/50',
                )} />
                <div>
                  <p className="text-[13px] leading-relaxed text-anchor-navy/95">{item.title}</p>
                  <p className="mt-1 text-[11px] text-anchor-fog/80">{item.detail}</p>
                  <p className="mt-1 font-mono text-[9px] tracking-[0.06em] text-anchor-fog/65">{humanTime(item.occurred_at, nowMs)}</p>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="px-5 py-6 sm:px-6">
            <p className="text-sm text-anchor-fog">No meaningful decisions yet.</p>
            <p className="mt-1 text-xs text-anchor-fog/60">
              This section highlights rebalances, guardrails, and meaningful execution changes.
            </p>
          </div>
        )}
      </section>

      {/* ── Trade history ───────────────────────────────────────────── */}
      <section className="panel-glass animate-anchor-fade-up-delayed overflow-hidden rounded-[24px] border border-white/7 shadow-[0_14px_36px_rgba(0,0,0,0.24)]">
        <div className="border-b border-white/7 px-5 py-4 sm:px-6">
          <h2 className="font-display text-[1.3rem] leading-tight text-anchor-brass sm:text-[1.45rem]">
            Trade history
          </h2>
          <p className="mt-0.5 text-xs text-anchor-fog">
            Closed trades — profit or loss is locked in permanently
          </p>
        </div>

        <div className="px-5 sm:px-6">
          {recentTrades.length > 0 ? (
            recentTrades.slice(0, 8).map((trade) => (
              <TradeRow key={trade.id} trade={trade} nowMs={nowMs} />
            ))
          ) : (
            <div className="py-6">
              <p className="text-sm text-anchor-fog">No closed trades yet.</p>
              <p className="mt-1 text-xs text-anchor-fog/60">
                When positions close, they'll appear here with the final profit or loss locked in.
              </p>
            </div>
          )}
        </div>
      </section>

      {/* ── Footer ─────────────────────────────────────────────────── */}
      <footer className="mt-10 flex items-center justify-center gap-3 pt-2">
        <div className="flex items-center gap-2 rounded-full border border-white/7 bg-white/[0.03] px-3 py-2 text-anchor-fog/55">
          <AnchorMark size={11} />
          <span className="font-mono text-[9px] uppercase tracking-[0.24em]">
            Anchor · personal futures monitor
          </span>
        </div>
      </footer>
    </div>
  )
}
