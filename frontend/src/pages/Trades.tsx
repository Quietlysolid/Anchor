import { useMemo, useState } from 'react'
import { useLatestSignals, useTradeExplanations, useTradeJournal } from '../api/hooks'
import type { Signal, Trade } from '../types'

function fmtET(isoStr: string, opts: Intl.DateTimeFormatOptions): string {
  return new Date(isoStr).toLocaleString('en-US', { timeZone: 'America/New_York', ...opts })
}

function fmtTimeShort(isoStr: string): string {
  return fmtET(isoStr, { hour: 'numeric', minute: '2-digit', hour12: true })
    .replace(' AM', 'a').replace(' PM', 'p')
}

function formatInstrument(instrument: string): string {
  return instrument.replace('_', '/')
}

function duration(openedAt: string, closedAt: string): string {
  const mins = Math.round((new Date(closedAt).getTime() - new Date(openedAt).getTime()) / 60_000)
  if (mins < 60) return `${mins}m`
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return m ? `${h}h ${m}m` : `${h}h`
}

function dayLabel(isoStr: string): string {
  const etNow = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }))
  const etD = new Date(new Date(isoStr).toLocaleString('en-US', { timeZone: 'America/New_York' }))
  const diffDays = Math.floor(
    (new Date(etNow.toDateString()).getTime() - new Date(etD.toDateString()).getTime()) / 86_400_000
  )
  if (diffDays === 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  return fmtET(isoStr, { weekday: 'long', month: 'long', day: 'numeric' })
}

function ageText(value: string): string {
  const secs = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000))
  if (secs < 60) return `${secs}s ago`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

function plainReason(reason: string | null | undefined): string {
  if (!reason) return 'no reason saved'
  const raw = reason.toUpperCase()
  if (raw.includes('OFF WINDOW') || raw.includes('OUTSIDE') || raw.includes('WINDOW')) return 'window wasn\'t open'
  if (raw.includes('SPREAD')) return 'spread was too wide'
  if (raw.includes('NEWS') || raw.includes('ECON')) return 'too close to news'
  if (raw.includes('HALT') || raw.includes('DISABLED') || raw.includes('PAUSE')) return 'I was paused'
  if (raw.includes('RISK') || raw.includes('DRAWDOWN') || raw.includes('LOSS')) return 'risk was already high'
  if (raw.includes('CONFIDENCE') || raw.includes('SCORE') || raw.includes('WEAK')) return 'setup wasn\'t there'
  if (raw.includes('HMM') || raw.includes('RANGING')) return 'still ranging'
  return raw.replace(/_/g, ' ').toLowerCase()
}

type BatchEntry = {
  id: string
  time: string
  kind: 'blocked_batch'
  count: number
  pairs: string[]
  reason: string
  tone: 'warn'
}

function collapseBlocked(entries: Entry[]): (Entry | BatchEntry)[] {
  const result: (Entry | BatchEntry)[] = []
  let i = 0
  while (i < entries.length) {
    const e = entries[i]
    if (e.kind !== 'trade_blocked') {
      result.push(e)
      i++
      continue
    }
    // collect a run of blocked entries with the same reason (any pair)
    const reason = e.summary
    let j = i + 1
    while (j < entries.length && entries[j].kind === 'trade_blocked' && entries[j].summary === reason) {
      j++
    }
    const run = entries.slice(i, j)
    if (run.length <= 2) {
      // small run — show individually
      run.forEach(x => result.push(x))
    } else {
      const pairs = [...new Set(run.map(x => x.pair))]
      result.push({
        id: `batch-${e.id}`,
        time: e.time,
        kind: 'blocked_batch',
        count: run.length,
        pairs,
        reason,
        tone: 'warn',
      })
    }
    i = j
  }
  return result
}

type Entry =
  | { id: string; time: string; kind: 'trade_closed'; pair: string; title: string; summary: string; detail: string; tone: 'good' | 'bad' }
  | { id: string; time: string; kind: 'trade_blocked' | 'trade_opened'; pair: string; title: string; summary: string; detail: string; tone: 'warn' | 'good' }

function EntryRow({ entry }: { entry: Entry }) {
  const [open, setOpen] = useState(false)

  const borderColor =
    entry.tone === 'good' ? 'border-anchor-green' :
    entry.tone === 'bad'  ? 'border-anchor-red' :
    'border-anchor-amber/60'

  const toneTextClass =
    entry.tone === 'good' ? 'text-anchor-green' :
    entry.tone === 'bad'  ? 'text-anchor-red' :
    'text-anchor-amber'

  const kindLabel =
    entry.kind === 'trade_closed' ? (entry.tone === 'good' ? 'won' : 'lost') :
    entry.kind === 'trade_blocked' ? 'skipped' : 'opened'

  return (
    <div className="flex gap-5 py-4 border-b border-anchor-rule/30 last:border-0">
      <div className="w-[60px] shrink-0 pt-0.5">
        <p className="font-mono text-[10px] text-anchor-muted/35 leading-none">
          {fmtTimeShort(entry.time)}
        </p>
      </div>

      <div className={`flex-1 pl-4 border-l-[1.5px] ${borderColor}`}>
        <button
          type="button"
          onClick={() => setOpen(v => !v)}
          className="w-full text-left"
        >
          <p className="text-sm font-medium text-anchor-text leading-snug">{entry.title}</p>
          <p className="text-xs text-anchor-muted mt-0.5 leading-relaxed">{entry.summary}</p>
        </button>
        {open && (
          <p className="text-xs text-anchor-muted/70 mt-2 leading-relaxed border-t border-anchor-rule/30 pt-2">
            {entry.detail}
          </p>
        )}
      </div>

      <div className="shrink-0 pt-0.5">
        <p className={`font-mono text-[9px] tracking-[0.1em] uppercase ${toneTextClass} opacity-60`}>
          {kindLabel}
        </p>
      </div>
    </div>
  )
}

function BatchRow({ batch }: { batch: BatchEntry }) {
  return (
    <div className="flex gap-5 py-3 border-b border-anchor-rule/30 last:border-0">
      <div className="w-[60px] shrink-0 pt-0.5">
        <p className="font-mono text-[10px] text-anchor-muted/25 leading-none">
          {fmtTimeShort(batch.time)}
        </p>
      </div>
      <div className="flex-1 pl-4 border-l-[1.5px] border-anchor-border/60">
        <p className="text-xs text-anchor-muted/70 leading-relaxed">
          Checked {batch.count}× — {batch.reason}.{' '}
          <span className="text-anchor-muted/40">{batch.pairs.join(', ')}</span>
        </p>
      </div>
    </div>
  )
}

export default function Trades() {
  const { data: apiTrades, isLoading, isError } = useTradeJournal()
  const { data: explanationData } = useTradeExplanations(50)
  const { data: signalData } = useLatestSignals()
  const trades: Trade[] = useMemo(() => apiTrades ?? [], [apiTrades])
  const signals: Signal[] = useMemo(() => signalData ?? [], [signalData])

  const explanations = useMemo(() => {
    const map: Record<string, string> = {}
    for (const ex of explanationData ?? []) {
      if (ex.trade_id) map[ex.trade_id] = ex.content
    }
    return map
  }, [explanationData])

  const entries = useMemo<Entry[]>(() => {
    const tradeEntries = trades.slice(0, 60).map(trade => {
      const won = trade.net_pl > 0
      const pair = formatInstrument(trade.instrument)
      const explanation = explanations[trade.id]
      const plStr = `${won ? '+' : '-'}$${Math.abs(trade.net_pl).toFixed(2)}`
      return {
        id: `trade-${trade.id}`,
        time: trade.closed_at,
        kind: 'trade_closed' as const,
        pair,
        title: `${pair} — ${won ? 'took it.' : 'got stopped.'}`,
        summary: `${trade.direction === 'LONG' ? 'Long' : 'Short'} · held ${duration(trade.opened_at, trade.closed_at)} · ${plStr}`,
        detail: explanation ?? `${pair} ${trade.direction === 'LONG' ? 'long' : 'short'} closed ${won ? 'profitable' : 'at a loss'}.`,
        tone: won ? 'good' as const : 'bad' as const,
      }
    })

    const signalEntries = signals.slice(0, 60).map(signal => {
      const pair = formatInstrument(signal.instrument)
      if (signal.suppressed) {
        return {
          id: `signal-${signal.id}`,
          time: signal.created_at,
          kind: 'trade_blocked' as const,
          pair,
          title: `${pair} — passed on it.`,
          summary: plainReason(signal.suppression_reason),
          detail: `Checked ${pair}. Didn't take it. ${plainReason(signal.suppression_reason)}.`,
          tone: 'warn' as const,
        }
      }
      return {
        id: `signal-${signal.id}`,
        time: signal.created_at,
        kind: 'trade_opened' as const,
        pair,
        title: `${pair} — ${signal.direction === 'LONG' ? 'bought.' : 'sold.'}`,
        summary: `${signal.direction === 'LONG' ? 'Long' : 'Short'} setup — all filters passed`,
        detail: `Opened a paper ${signal.direction === 'LONG' ? 'long' : 'short'} in ${pair}. Setup was clean.`,
        tone: 'good' as const,
      }
    })

    return [...tradeEntries, ...signalEntries]
      .sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime())
      .slice(0, 120)
  }, [explanations, signals, trades])

  const grouped = useMemo(() => {
    const groups: Record<string, Entry[]> = {}
    for (const entry of entries) {
      const label = dayLabel(entry.time)
      if (!groups[label]) groups[label] = []
      groups[label].push(entry)
    }
    return Object.entries(groups).map(([day, dayEntries]) => ({
      day,
      rows: collapseBlocked(dayEntries),
    }))
  }, [entries])

  const totalBlocked = signals.filter(s => s.suppressed).length
  const latestEntry = entries[0]

  return (
    <div className="min-h-screen bg-anchor-void pb-20 md:pb-0">

      {/* ── Header ────────────────────────────────────────────── */}
      <div className="px-8 pt-10 pb-9 md:px-12 md:pt-12 border-b border-anchor-rule">
        <p className="font-mono text-[9px] tracking-[0.32em] uppercase text-anchor-muted/45 mb-6">Log</p>
        <h1 className="font-serif text-[3.2rem] md:text-[4.4rem] leading-[0.92] tracking-[-0.02em] text-anchor-text">
          What I tried.
        </h1>
        <p className="mt-4 text-base text-anchor-muted max-w-lg leading-relaxed">
          Every trade I took, every setup I skipped, and why.
          {' '}Paper only — no real cash at risk.
        </p>

        {/* Inline stats */}
        <div className="mt-8 flex flex-wrap gap-x-8 gap-y-3">
          <div>
            <p className="font-mono text-[9px] tracking-[0.28em] uppercase text-anchor-muted/45 mb-1">Closed</p>
            <p className="text-xl font-semibold tracking-[-0.03em] text-anchor-text">{trades.length}</p>
          </div>
          <div>
            <p className="font-mono text-[9px] tracking-[0.28em] uppercase text-anchor-muted/45 mb-1">Wins</p>
            <p className="text-xl font-semibold tracking-[-0.03em] text-anchor-green">
              {trades.filter(t => t.net_pl > 0).length}
            </p>
          </div>
          <div>
            <p className="font-mono text-[9px] tracking-[0.28em] uppercase text-anchor-muted/45 mb-1">Skipped</p>
            <p className="text-xl font-semibold tracking-[-0.03em] text-anchor-muted">{totalBlocked}</p>
          </div>
          {latestEntry && (
            <div>
              <p className="font-mono text-[9px] tracking-[0.28em] uppercase text-anchor-muted/45 mb-1">Last active</p>
              <p className="text-xl font-semibold tracking-[-0.03em] text-anchor-text">{ageText(latestEntry.time)}</p>
            </div>
          )}
        </div>
      </div>

      {/* ── Loading / Error / Empty states ──────────────────── */}
      {isLoading && (
        <div className="px-8 py-16 md:px-12">
          <p className="text-sm text-anchor-muted">Loading diary…</p>
        </div>
      )}

      {isError && !isLoading && (
        <div className="px-8 py-16 md:px-12">
          <p className="text-sm text-anchor-red">Diary is unavailable right now.</p>
        </div>
      )}

      {!isLoading && !isError && grouped.length === 0 && (
        <div className="px-8 py-16 md:px-12">
          <p className="text-sm text-anchor-muted">Nothing yet. I'll fill this in when something happens.</p>
        </div>
      )}

      {/* ── Day groups ──────────────────────────────────────── */}
      {!isLoading && !isError && grouped.length > 0 && (
        <div>
          {grouped.map(({ day, rows }) => (
            <div key={day} className="border-b border-anchor-rule last:border-0">
              <div className="px-8 md:px-12 flex items-baseline gap-5 pt-9 pb-6">
                <h2 className="font-serif text-[2.4rem] md:text-[3.2rem] leading-none tracking-[-0.02em] text-anchor-text">
                  {day}
                </h2>
                <p className="font-mono text-[10px] tracking-[0.2em] uppercase text-anchor-muted/35">
                  {rows.length} {rows.length === 1 ? 'entry' : 'entries'}
                </p>
              </div>

              <div className="px-8 md:px-12 pb-6">
                {rows.map(row =>
                  row.kind === 'blocked_batch'
                    ? <BatchRow key={row.id} batch={row} />
                    : <EntryRow key={row.id} entry={row} />
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
