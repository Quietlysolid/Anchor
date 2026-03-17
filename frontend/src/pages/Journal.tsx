import { useState } from 'react'
import { useTradeJournal, useTradeExplanations, useJournalAnalysis } from '../api/hooks'
import { TradeJournalTable } from '../components/tables/TradeJournalTable'
import { useWeightsStore } from '../store'

function utcToET(h: number, m: number): string {
  const d = new Date()
  d.setUTCHours(h, m, 0, 0)
  return d.toLocaleTimeString('en-US', {
    timeZone: 'America/New_York',
    hour: 'numeric', minute: '2-digit', hour12: true,
  })
}

export default function Journal() {
  const { data: trades, isLoading } = useTradeJournal()
  const { data: explanations } = useTradeExplanations(20)
  const { data: journalAnalysis } = useJournalAnalysis()
  const { win_rate_good, win_rate_warn } = useWeightsStore(s => s.targets)
  const [expandedAnalysis, setExpandedAnalysis] = useState(false)

  // Build a lookup: trade_id → explanation text
  const explanationMap = Object.fromEntries(
    (explanations ?? [])
      .filter(e => e.trade_id)
      .map(e => [e.trade_id!, e.content])
  )

  const wins    = trades?.filter(t => t.net_pl > 0).length ?? 0
  const losses  = trades?.filter(t => t.net_pl < 0).length ?? 0
  const total   = (trades ?? []).reduce((s, t) => s + t.net_pl, 0)
  const count   = trades?.length ?? 0
  const winRate = count > 0 ? wins / count : null

  const wrColor = winRate === null ? ''
    : winRate >= win_rate_good ? 'text-green-400'
    : winRate >= win_rate_warn ? 'text-amber-400'
    : 'text-red-400'

  const stats = [
    {
      label: 'Trades taken',
      value: String(count),
      sub: 'total since launch',
    },
    {
      label: 'Profitable',
      value: String(wins),
      cls: wins > 0 ? 'text-green-400' : '',
      sub: 'closed in profit',
    },
    {
      label: 'Losses',
      value: String(losses),
      cls: losses > 0 ? 'text-red-400' : '',
      sub: 'closed at a loss',
    },
    {
      label: 'Success rate',
      value: winRate !== null ? `${(winRate * 100).toFixed(1)}%` : '—',
      cls: wrColor,
      sub: winRate !== null
        ? winRate >= win_rate_good ? '✓ Above target' : `Target: ${Math.round(win_rate_good * 100)}%+`
        : `Target: ${Math.round(win_rate_good * 100)}%+`,
    },
    {
      label: 'Total profit / loss',
      value: count > 0 ? `${total >= 0 ? '+' : ''}$${total.toFixed(2)}` : '—',
      cls: total >= 0 ? 'text-green-400' : 'text-red-400',
      sub: 'net after all trades',
    },
  ]

  return (
    <div className="p-4 md:p-6 space-y-4 md:space-y-6">
      <div>
        <h1 className="text-xl font-bold">Trade Journal</h1>
        <p className="text-sm text-muted-foreground mt-0.5">Every trade the system has placed, closed, and recorded.</p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        {stats.map(({ label, value, cls, sub }) => (
          <div key={label} className="bg-card border border-border rounded-lg p-4">
            <div className={`text-2xl font-bold font-mono ${cls ?? ''}`}>{value}</div>
            <div className="text-xs font-medium mt-1">{label}</div>
            {sub && <div className="text-[10px] text-muted-foreground/50 mt-0.5">{sub}</div>}
          </div>
        ))}
      </div>

      {/* Weekly Journal Analysis */}
      {journalAnalysis && (
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <div>
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">AI Pattern Analysis</span>
              <span className="ml-2 text-[10px] text-muted-foreground/50">
                {new Date(journalAnalysis.created_at).toLocaleDateString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric' })}
                {journalAnalysis.trade_count != null && ` · ${journalAnalysis.trade_count} trades`}
              </span>
            </div>
            <button
              onClick={() => setExpandedAnalysis(x => !x)}
              className="text-xs text-primary hover:underline"
            >
              {expandedAnalysis ? 'Hide' : 'Show analysis'}
            </button>
          </div>
          {expandedAnalysis && (
            <pre className="text-xs text-muted-foreground whitespace-pre-wrap leading-relaxed mt-2 font-sans">
              {journalAnalysis.content}
            </pre>
          )}
        </div>
      )}

      <div className="bg-card border border-border rounded-lg p-4">
        {isLoading ? (
          <div className="space-y-3 py-4">
            {[...Array(6)].map((_, i) => (
              <div key={i} className="skeleton h-4 w-full" style={{ opacity: 1 - i * 0.12 }} />
            ))}
          </div>
        ) : !trades?.length ? (
          <div className="text-center py-12">
            <p className="text-muted-foreground text-sm font-medium">No trades yet</p>
            <p className="text-muted-foreground/50 text-xs mt-2 max-w-xs mx-auto leading-relaxed">
              The system will place its first trade during the morning session ({utcToET(7, 15)} – {utcToET(12, 0)} ET)
              or the evening reversal window ({utcToET(17, 0)} – {utcToET(20, 0)} ET) when a strong enough setup appears.
            </p>
          </div>
        ) : (
          <TradeJournalTable trades={trades} explanations={explanationMap} />
        )}
      </div>
    </div>
  )
}
