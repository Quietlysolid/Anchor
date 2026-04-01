import { useEffect, useMemo, useState } from 'react'
import { useHomepageSnapshot } from '../api/hooks'
import { usePositionStore, useSystemStore } from '../store'
import type { Direction, HomepageSnapshotActivity, HomepageSnapshotResult } from '../types'

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
  return d === 'LONG' ? 'Betting it goes UP' : 'Betting it goes DOWN'
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
  if (hrs  > 0) return `${hrs}h ${m}m`
  return `${m}m`
}

function cn(...cls: Array<string | false | null | undefined>) {
  return cls.filter(Boolean).join(' ')
}

// ── Activity noise filter ────────────────────────────────────────────────────
// These are backend-brain events the user doesn't need to see in their journal.

const NOISE_REASONS = new Set([
  'RECONCILIATION', 'LIVE_PERF_CHECK', 'EDGE_CONFIDENCE_CHECK',
])
const NOISE_KINDS = new Set([
  'signal_blocked', 'signal_blocked_batch',
])

function isUseful(item: HomepageSnapshotActivity): boolean {
  if (NOISE_REASONS.has((item.reason_code ?? '').toUpperCase())) return false
  if (NOISE_KINDS.has(item.kind)) return false
  return true
}

function toPlainEnglish(item: HomepageSnapshotActivity, nowMs: number) {
  const mkt  = item.instrument ? marketInfo(item.instrument).name : null
  const time = humanTime(item.occurred_at, nowMs)
  let text: string

  if (item.kind === 'trade_closed') {
    text = mkt ? `${mkt} position was closed.` : 'A position was closed.'
  } else if (item.kind === 'order') {
    text = mkt ? `New order placed for ${mkt}.` : 'An order was placed.'
  } else if (item.kind === 'signal_passed') {
    text = mkt
      ? `${mkt} — signal still active, holding position.`
      : 'Signal active, holding.'
  } else {
    text = item.title || 'System updated.'
  }

  return { text, time, tone: item.tone }
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
    <div className="flex items-start justify-between gap-3 border-b border-anchor-rule/30 py-4 last:border-0">
      <div>
        <p className="text-sm font-semibold text-anchor-navy">{mkt.name}</p>
        <p className="mt-0.5 text-sm text-anchor-slate">
          {trade.direction === 'LONG' ? 'Bet on going up' : 'Bet on going down'}
          {'  ·  '}held {holdTime(trade.opened_at, closeMs)}
        </p>
        <p className="mt-0.5 font-mono text-[10px] text-anchor-fog">
          Closed {humanTime(trade.closed_at, nowMs)}
        </p>
      </div>
      <div className="shrink-0 text-right">
        <p className={cn('font-mono text-base font-bold', won ? 'text-anchor-win' : 'text-anchor-loss')}>
          {signedDollars(trade.net_pl)}
        </p>
        <p className={cn(
          'mt-0.5 font-mono text-[9px] uppercase tracking-widest',
          won ? 'text-anchor-win/70' : 'text-anchor-loss/70',
        )}>
          {won ? 'locked in' : 'locked in loss'}
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
  const mode          = account?.mode ?? 'paper'
  const equity        = wsEquity > 0 ? wsEquity : (account?.equity ?? 0)
  const todayPL       = account?.today_pl ?? null
  const nextRebalance = snapshot?.operator?.next_window?.starts_at
  const recentTrades  = snapshot?.recent_results ?? []
  const degraded      = snapshot?.operator?.operator_state === 'DEGRADED'
  const running       = wsConnected && !degraded

  // Total unrealized P&L across all open positions
  const totalPaperPL = positions.reduce((s, p) => s + (p.unrealized_pl ?? 0), 0)

  // Journal: strip reconciliation spam, translate to plain English
  const journal = useMemo(() =>
    (snapshot?.activity ?? [])
      .filter(isUseful)
      .map(item => toPlainEnglish(item, nowMs))
      .slice(0, 8),
    [snapshot?.activity, nowMs],
  )

  return (
    <div className="mx-auto max-w-2xl px-4 pb-16 pt-5 sm:px-6">

      {/* ── Status bar ─────────────────────────────────────────────── */}
      <div className={cn(
        'mb-6 flex flex-wrap items-center gap-x-3 gap-y-2 border px-4 py-3',
        running
          ? 'border-anchor-win/20 bg-anchor-win/[0.03]'
          : 'border-anchor-loss/25 bg-anchor-loss/[0.04]',
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

        <span className="text-anchor-rule/40">·</span>

        <span className="font-mono text-[11px] tracking-wider text-anchor-fog">
          {mode === 'paper' ? 'Paper money' : 'Live account'}
        </span>

        {/* Equity + today P&L pushed to the right */}
        <div className="ml-auto flex items-baseline gap-2">
          <span className="font-mono text-[11px] font-bold text-anchor-navy">
            {dollars(equity)}
          </span>
          {todayPL != null && (
            <span className={cn(
              'font-mono text-[11px] font-semibold',
              todayPL > 0 ? 'text-anchor-win'
                          : todayPL < 0 ? 'text-anchor-loss'
                          : 'text-anchor-fog',
            )}>
              {signedDollars(todayPL)} today
            </span>
          )}
        </div>
      </div>

      {/* ── Portfolio ──────────────────────────────────────────────── */}
      <section className="panel-glass mb-5 overflow-hidden border border-anchor-navy/[0.09] shadow-[0_12px_36px_rgba(26,39,68,0.05)]">

        {/* Card header */}
        <div className="flex items-start justify-between gap-4 border-b border-anchor-rule/30 px-5 py-4 sm:px-6">
          <div>
            <h2 className="font-display text-[1.35rem] leading-tight text-anchor-navy sm:text-[1.5rem]">
              Your portfolio
            </h2>
            {positions.length > 0 && totalPaperPL !== 0 && (
              <p className={cn(
                'mt-1 text-sm leading-snug',
                totalPaperPL > 0 ? 'text-anchor-win' : 'text-anchor-loss',
              )}>
                {signedDollars(totalPaperPL)}{' '}
                <span className="text-anchor-fog/70 text-xs font-normal">
                  on paper right now
                </span>
              </p>
            )}
          </div>
          <p className="shrink-0 font-mono text-xl font-bold text-anchor-navy sm:text-2xl">
            {dollars(equity)}
          </p>
        </div>

        {/* Open positions — each one is its own row */}
        {positions.length > 0 ? (
          <>
            <div className="divide-y divide-anchor-rule/25">
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

                return (
                  <div key={pos.id} className="px-5 py-4 sm:px-6">
                    {/* Name + live P&L */}
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <p className="font-semibold leading-snug text-anchor-navy">{mkt.name}</p>
                        <p className="mt-0.5 text-xs text-anchor-fog">{mkt.desc}</p>
                      </div>
                      <div className="shrink-0 text-right">
                        <p className={cn(
                          'font-mono text-lg font-bold leading-tight',
                          plUp ? 'text-anchor-win' : plDn ? 'text-anchor-loss' : 'text-anchor-navy/40',
                        )}>
                          {signedDollars(pl)}
                        </p>
                        <p className="mt-0.5 font-mono text-[9px] uppercase tracking-widest text-anchor-fog">
                          on paper
                        </p>
                      </div>
                    </div>

                    {/* Position details */}
                    <div className="mt-3 space-y-1.5 border-t border-anchor-rule/20 pt-3">
                      <p className="text-sm text-anchor-slate">
                        <span className={cn(
                          'font-semibold',
                          pos.direction === 'LONG' ? 'text-anchor-win' : 'text-anchor-loss',
                        )}>
                          {directionLabel(pos.direction)}
                        </span>
                        {'  ·  '}{Math.abs(pos.units)} contract{Math.abs(pos.units) !== 1 ? 's' : ''}
                      </p>

                      <p className="text-sm text-anchor-slate">
                        Entered at{' '}
                        <span className="font-mono font-semibold text-anchor-navy">
                          {priceStr(pos.avg_entry_price)}
                        </span>
                        {'  ·  '}holding for{' '}
                        <span className="font-semibold text-anchor-navy">
                          {holdTime(pos.opened_at, nowMs)}
                        </span>
                      </p>

                      {pos.current_price != null && (
                        <p className="text-sm text-anchor-slate">
                          Currently at{' '}
                          <span className="font-mono font-semibold text-anchor-navy">
                            {priceStr(pos.current_price)}
                          </span>
                        </p>
                      )}

                      {exitHint && (
                        <p className="text-sm text-anchor-fog">{exitHint}</p>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>

            {/* Plain-English note on unrealized P&L */}
            <div className="border-t border-anchor-rule/25 bg-anchor-parchment/40 px-5 py-3 sm:px-6">
              <p className="text-xs leading-relaxed text-anchor-fog">
                <span className="font-semibold text-anchor-slate">Paper profit/loss</span> moves with
                the market but isn't real yet — it locks in permanently when a trade closes and becomes
                part of your account balance.
              </p>
            </div>
          </>
        ) : (
          <div className="px-5 py-8 text-center sm:px-6">
            <p className="text-sm text-anchor-fog">No positions open right now.</p>
            <p className="mt-1.5 text-xs text-anchor-fog/60">
              Anchor checks for signals every day. Trades will appear here when a position is open.
            </p>
          </div>
        )}

        {/* Next rebalance hint */}
        {nextRebalance && (
          <div className="border-t border-anchor-rule/25 px-5 py-3 sm:px-6">
            <p className="text-xs text-anchor-fog">
              Next rebalance:{' '}
              <span className="font-semibold text-anchor-navy">
                {etFmt(nextRebalance, { weekday: 'short', hour: 'numeric', minute: '2-digit', hour12: true })} ET
              </span>
              {'  —  '}Anchor reviews every market and adjusts positions if needed
            </p>
          </div>
        )}
      </section>

      {/* ── What Anchor did (journal) ───────────────────────────────── */}
      <section className="panel-glass mb-5 overflow-hidden border border-anchor-navy/[0.09] shadow-[0_12px_36px_rgba(26,39,68,0.05)]">
        <div className="border-b border-anchor-rule/30 px-5 py-4 sm:px-6">
          <h2 className="font-display text-[1.35rem] leading-tight text-anchor-navy sm:text-[1.5rem]">
            What Anchor did
          </h2>
          <p className="mt-0.5 text-xs text-anchor-fog">
            Trade events and notable moments, in plain English
          </p>
        </div>

        {journal.length > 0 ? (
          <div className="divide-y divide-anchor-rule/25">
            {journal.map((item, i) => (
              <div key={i} className="flex gap-3 px-5 py-3.5 sm:px-6">
                <div className={cn(
                  'mt-1.5 h-2 w-2 shrink-0 rounded-full',
                  item.tone === 'good' ? 'bg-anchor-win' :
                  item.tone === 'bad'  ? 'bg-anchor-loss' :
                  item.tone === 'warn' ? 'bg-anchor-warn' :
                  'bg-anchor-rule/50',
                )} />
                <div>
                  <p className="text-sm text-anchor-navy">{item.text}</p>
                  <p className="mt-0.5 font-mono text-[10px] text-anchor-fog">{item.time}</p>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="px-5 py-6 sm:px-6">
            <p className="text-sm text-anchor-fog">Nothing notable yet today.</p>
            <p className="mt-1 text-xs text-anchor-fog/60">
              Anchor logs trades, rebalances, and important events here — not every heartbeat.
            </p>
          </div>
        )}
      </section>

      {/* ── Trade history ───────────────────────────────────────────── */}
      <section className="panel-glass overflow-hidden border border-anchor-navy/[0.09] shadow-[0_12px_36px_rgba(26,39,68,0.05)]">
        <div className="border-b border-anchor-rule/30 px-5 py-4 sm:px-6">
          <h2 className="font-display text-[1.35rem] leading-tight text-anchor-navy sm:text-[1.5rem]">
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
      <footer className="mt-8 flex items-center justify-between gap-3 border-t border-anchor-rule/35 pt-4">
        <div className="flex items-center gap-2 text-anchor-rule/50">
          <AnchorMark size={11} />
          <span className="font-mono text-[9px] uppercase tracking-widest">
            Anchor · personal futures monitor
          </span>
        </div>
        <span className="font-mono text-[9px] uppercase tracking-widest text-anchor-fog">
          {mode === 'paper' ? 'Paper — no real money at risk' : 'Live trading'}
        </span>
      </footer>
    </div>
  )
}
