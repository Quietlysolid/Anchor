import { format } from 'date-fns'
import type { Trade } from '../../types'

interface Props { trades: Trade[] }

export function TradeJournalTable({ trades }: Props) {
  return (
    <>
      {/* Desktop table */}
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted-foreground border-b border-border">
              {['Closed','Instrument','Dir','Entry','Exit','Net P&L','Reason','Session','Regime'].map(h => (
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
                  <td className="py-2 pr-4">
                    {t.session_at_entry === 'NY_LCR'
                      ? <span className="text-purple-400 text-xs">NY LCR</span>
                      : <span className="text-muted-foreground">{t.session_at_entry ?? '—'}</span>
                    }
                  </td>
                  <td className="py-2 text-muted-foreground">{t.regime_at_entry ?? '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Mobile card list */}
      <div className="sm:hidden space-y-2">
        {trades.map(t => {
          const plColor = (t.net_pl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
          const plSign  = (t.net_pl ?? 0) >= 0 ? '+' : ''
          return (
            <div key={t.id} className="bg-muted/30 rounded-lg p-3 text-xs space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="font-mono font-bold">{t.instrument.replace('_','/')}</span>
                <span className={`font-mono font-bold ${plColor}`}>{plSign}{(t.net_pl ?? 0).toFixed(2)}</span>
              </div>
              <div className="flex items-center justify-between text-muted-foreground">
                <span className={`font-bold ${t.direction === 'LONG' ? 'text-green-400' : 'text-red-400'}`}>{t.direction}</span>
                <span>{format(new Date(t.closed_at), 'MMM d HH:mm')}</span>
              </div>
              <div className="flex items-center justify-between text-muted-foreground">
                <span>{t.close_reason}</span>
                {t.session_at_entry === 'NY_LCR'
                  ? <span className="text-purple-400">NY LCR</span>
                  : <span>{t.session_at_entry ?? '—'}</span>
                }
              </div>
            </div>
          )
        })}
      </div>
    </>
  )
}