import { format } from 'date-fns'
import type { Trade } from '../../types'

interface Props { trades: Trade[] }

export function TradeJournalTable({ trades }: Props) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-muted-foreground border-b border-border">
            {['Closed','Instrument','Dir','Entry','Exit','Net P&L','Reason','Regime'].map(h => (
              <th key={h} className="text-left pb-2 pr-4 font-normal whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {trades.map(t => {
            const plColor = (t.net_pl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
            return (
              <tr key={t.id} className="border-b border-border/30 hover:bg-muted/20">
                <td className="py-2 pr-4 text-muted-foreground whitespace-nowrap">{format(new Date(t.closed_at), 'MMM d HH:mm')}</td>
                <td className="py-2 pr-4 font-mono">{t.instrument.replace('_','/')}</td>
                <td className={`py-2 pr-4 font-bold ${t.direction === 'LONG' ? 'text-green-400' : 'text-red-400'}`}>{t.direction}</td>
                <td className="py-2 pr-4 font-mono">{(t.entry_price ?? 0).toFixed(5)}</td>
                <td className="py-2 pr-4 font-mono">{(t.exit_price ?? 0).toFixed(5)}</td>
                <td className={`py-2 pr-4 font-mono font-bold ${plColor}`}>{(t.net_pl ?? 0) >= 0 ? '+' : ''}{(t.net_pl ?? 0).toFixed(2)}</td>
                <td className="py-2 pr-4 text-muted-foreground">{t.close_reason}</td>
                <td className="py-2 text-muted-foreground">{t.regime_at_entry ?? '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}