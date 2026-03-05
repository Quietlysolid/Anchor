import { useTradeJournal } from '../api/hooks'
import { TradeJournalTable } from '../components/tables/TradeJournalTable'

export default function Journal() {
  const { data: trades, isLoading } = useTradeJournal()

  const wins   = trades?.filter(t => t.net_pl > 0).length ?? 0
  const losses = trades?.filter(t => t.net_pl < 0).length ?? 0
  const total  = (trades ?? []).reduce((s, t) => s + t.net_pl, 0)

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-xl font-bold">Trade Journal</h1>

      <div className="grid grid-cols-4 gap-4 text-center">
        {[
          { label: 'Total Trades', value: String(trades?.length ?? 0) },
          { label: 'Wins', value: String(wins), cls: 'text-green-400' },
          { label: 'Losses', value: String(losses), cls: 'text-red-400' },
          { label: 'Net P&L', value: `$${total.toFixed(2)}`, cls: total >= 0 ? 'text-green-400' : 'text-red-400' },
        ].map(({ label, value, cls }) => (
          <div key={label} className="bg-card border border-border rounded-lg p-4">
            <div className={`text-2xl font-bold font-mono ${cls ?? ''}`}>{value}</div>
            <div className="text-xs text-muted-foreground mt-1">{label}</div>
          </div>
        ))}
      </div>

      <div className="bg-card border border-border rounded-lg p-4">
        {isLoading ? (
          <p className="text-muted-foreground text-sm text-center py-8">Loading trades…</p>
        ) : !trades?.length ? (
          <p className="text-muted-foreground text-sm text-center py-8">No trades recorded yet</p>
        ) : (
          <TradeJournalTable trades={trades} />
        )}
      </div>
    </div>
  )
}