import { useMemo, useState } from 'react'
import { useTradeJournal, useTradeExplanations } from '../api/hooks'
import type { Trade } from '../types'
import { ChevronDown, ChevronUp } from 'lucide-react'

function fmtET(isoStr: string, opts: Intl.DateTimeFormatOptions): string {
  return new Date(isoStr).toLocaleString('en-US', { timeZone: 'America/New_York', ...opts })
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
  const etD   = new Date(new Date(isoStr).toLocaleString('en-US', { timeZone: 'America/New_York' }))
  const diffDays = Math.floor(
    (new Date(etNow.toDateString()).getTime() - new Date(etD.toDateString()).getTime()) / 86_400_000
  )
  if (diffDays === 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  return fmtET(isoStr, { weekday: 'long', month: 'short', day: 'numeric' })
}

function TradeRow({ trade, explanation }: { trade: Trade; explanation?: string }) {
  const [open, setOpen] = useState(false)
  const won    = trade.net_pl > 0
  const plAbs  = Math.abs(trade.net_pl)
  const isLong = trade.direction === 'LONG'
  const pair   = trade.instrument.replace('_', '/')
  const time   = fmtET(trade.closed_at, { hour: 'numeric', minute: '2-digit', hour12: true })
  const dur    = duration(trade.opened_at, trade.closed_at)

  const plainText = explanation
    ?? `The bot ${isLong ? 'bought' : 'sold'} ${pair} and held for ${dur}, closing with a ${won ? 'profit' : 'loss'} of $${plAbs.toFixed(2)}.`

  return (
    <div className="border-b border-anchor-border/40 last:border-0">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between py-3.5 gap-3 text-left"
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className={`w-1.5 h-8 rounded-full shrink-0 ${won ? 'bg-anchor-green' : 'bg-anchor-red'}`} />
          <div className="min-w-0">
            <p className="text-anchor-text font-medium text-sm">{pair}</p>
            <p className="text-anchor-muted text-xs mt-0.5">{time} · {dur}</p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <p className={`font-mono font-semibold text-sm ${won ? 'text-anchor-green' : 'text-anchor-red'}`}>
            {won ? '+' : '-'}${plAbs.toFixed(2)}
          </p>
          {open
            ? <ChevronUp   size={14} className="text-anchor-muted" />
            : <ChevronDown size={14} className="text-anchor-muted" />
          }
        </div>
      </button>

      {open && (
        <div className="pb-4 pl-6 pr-2">
          <p className="text-anchor-muted text-sm leading-relaxed">{plainText}</p>
        </div>
      )}
    </div>
  )
}

export default function History() {
  const { data: apiTrades }       = useTradeJournal()
  const { data: explanationData } = useTradeExplanations(50)
  const trades: Trade[] = useMemo(() => apiTrades ?? [], [apiTrades])

  const explanations = useMemo(() => {
    const map: Record<string, string> = {}
    for (const ex of explanationData ?? []) {
      if (ex.trade_id) map[ex.trade_id] = ex.content
    }
    return map
  }, [explanationData])

  // Monthly summary cards — newest first
  const monthlyStats = useMemo(() => {
    const months: Record<string, { pl: number; wins: number; losses: number; sortKey: number }> = {}
    for (const t of trades) {
      const label = fmtET(t.closed_at, { year: 'numeric', month: 'short' })
      if (!months[label]) months[label] = { pl: 0, wins: 0, losses: 0, sortKey: new Date(t.closed_at).getTime() }
      months[label].pl += t.net_pl
      if (t.net_pl > 0) months[label].wins++
      else months[label].losses++
    }
    return Object.entries(months).sort((a, b) => b[1].sortKey - a[1].sortKey)
  }, [trades])

  // Trades grouped by ET day, newest first
  const grouped = useMemo(() => {
    const groups: Record<string, { trades: Trade[]; sortKey: number }> = {}
    for (const t of trades) {
      const label = dayLabel(t.closed_at)
      if (!groups[label]) groups[label] = { trades: [], sortKey: new Date(t.closed_at).getTime() }
      groups[label].trades.push(t)
    }
    return Object.entries(groups).sort((a, b) => b[1].sortKey - a[1].sortKey)
  }, [trades])

  return (
    <div className="min-h-screen bg-anchor-void px-4 pt-4 pb-24 md:pb-8 space-y-5">

      {/* Monthly summary — horizontal scroll */}
      {monthlyStats.length > 0 && (
        <div>
          <p className="text-anchor-muted text-xs mb-3 px-1">Monthly Summary</p>
          <div className="flex gap-3 overflow-x-auto pb-1 -mx-4 px-4" style={{ scrollbarWidth: 'none' }}>
            {monthlyStats.map(([label, stats]) => (
              <div
                key={label}
                className={`shrink-0 rounded-2xl p-4 w-[118px] border ${
                  stats.pl >= 0
                    ? 'bg-anchor-green/5 border-anchor-green/20'
                    : 'bg-anchor-red/5  border-anchor-red/20'
                }`}
              >
                <p className="text-anchor-muted text-[11px] font-medium mb-2">{label}</p>
                <p className={`font-mono font-semibold text-base ${stats.pl >= 0 ? 'text-anchor-green' : 'text-anchor-red'}`}>
                  {stats.pl >= 0 ? '+' : '-'}${Math.abs(stats.pl).toFixed(0)}
                </p>
                <p className="text-anchor-muted text-[11px] mt-1.5">{stats.wins}W · {stats.losses}L</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Trade list grouped by day */}
      {grouped.length === 0 ? (
        <div className="bg-anchor-surface rounded-2xl p-8 text-center">
          <p className="text-anchor-muted text-sm">No trades yet</p>
          <p className="text-anchor-muted/50 text-xs mt-1">Your trade history will appear here</p>
        </div>
      ) : (
        <div className="space-y-4">
          {grouped.map(([day, { trades: dayTrades }]) => (
            <div key={day} className="bg-anchor-surface rounded-2xl px-4 py-1">
              <p className="text-anchor-muted text-xs font-medium py-3 border-b border-anchor-border/40">
                {day}
              </p>
              {dayTrades.map(t => (
                <TradeRow
                  key={t.id}
                  trade={t}
                  explanation={explanations[t.id]}
                />
              ))}
            </div>
          ))}
        </div>
      )}

    </div>
  )
}
