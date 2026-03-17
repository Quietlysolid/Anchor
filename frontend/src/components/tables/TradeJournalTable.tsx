import { Fragment, useState } from 'react'
import { format } from 'date-fns'
import type { Trade } from '../../types'

interface Props {
  trades: Trade[]
  explanations?: Record<string, string>  // trade_id → explanation text
}

function sessionLabel(s: string | null | undefined): { text: string; color: string } {
  if (s === 'NY_LCR') return { text: 'Evening reversal', color: 'text-purple-400' }
  if (s === 'LONDON')  return { text: 'London',          color: 'text-green-400/80' }
  return { text: s ?? '—', color: 'text-muted-foreground' }
}

function outcomeLabel(r: string | null | undefined): { text: string; color: string } {
  if (!r) return { text: '—', color: 'text-muted-foreground' }
  const map: Record<string, { text: string; color: string }> = {
    TP_HIT:        { text: 'Hit target ✓',  color: 'text-green-400' },
    SL_HIT:        { text: 'Stopped out',   color: 'text-red-400' },
    MANUAL:        { text: 'Manual close',  color: 'text-muted-foreground' },
    TIMEOUT:       { text: 'Time exit',     color: 'text-muted-foreground' },
    TRAILING_STOP: { text: 'Trailing stop', color: 'text-amber-400' },
    MARKET_CLOSE:  { text: 'Market close',  color: 'text-muted-foreground' },
    DRAWDOWN_HALT: { text: 'Risk halt',     color: 'text-red-400/80' },
  }
  return map[r] ?? { text: r.replace(/_/g, ' ').toLowerCase(), color: 'text-muted-foreground' }
}

export function TradeJournalTable({ trades, explanations = {} }: Props) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const toggle = (id: string) => setExpanded(x => ({ ...x, [id]: !x[id] }))

  return (
    <>
      {/* Desktop table */}
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted-foreground border-b border-border">
              {['Closed', 'Pair', 'Direction', 'Profit / Loss', 'Outcome', 'Session', 'Market', ''].map(h => (
                <th key={h} className="text-left pb-2 pr-5 font-normal whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {trades.map(t => {
              const pl          = t.net_pl ?? 0
              const plColor     = pl >= 0 ? 'text-green-400' : 'text-red-400'
              const session     = sessionLabel(t.session_at_entry)
              const outcome     = outcomeLabel(t.close_reason)
              const explanation = explanations[t.id]
              const isExpanded  = expanded[t.id]
              return (
                <Fragment key={t.id}>
                  <tr className="border-b border-border/30 hover:bg-muted/20 transition-colors">
                    <td className="py-2.5 pr-5 text-muted-foreground whitespace-nowrap">
                      {format(new Date(t.closed_at), 'MMM d, h:mm a')}
                    </td>
                    <td className="py-2.5 pr-5 font-mono font-medium">{t.instrument.replace('_', '/')}</td>
                    <td className={`py-2.5 pr-5 font-bold ${t.direction === 'LONG' ? 'text-green-400' : 'text-red-400'}`}>
                      {t.direction === 'LONG' ? '↑ Buy' : '↓ Sell'}
                    </td>
                    <td className={`py-2.5 pr-5 font-mono font-bold tabular-nums ${plColor}`}>
                      {pl >= 0 ? '+' : '−'}${Math.abs(pl).toFixed(2)}
                    </td>
                    <td className={`py-2.5 pr-5 ${outcome.color}`}>{outcome.text}</td>
                    <td className={`py-2.5 pr-5 ${session.color}`}>{session.text}</td>
                    <td className="py-2.5 pr-5 text-muted-foreground capitalize">
                      {t.regime_at_entry?.toLowerCase() ?? '—'}
                    </td>
                    <td className="py-2.5">
                      {explanation && (
                        <button
                          onClick={() => toggle(t.id)}
                          className="text-primary/70 hover:text-primary text-[10px] whitespace-nowrap"
                          title="AI explanation"
                        >
                          {isExpanded ? '▲ hide' : '▼ why'}
                        </button>
                      )}
                    </td>
                  </tr>
                  {explanation && isExpanded && (
                    <tr className="border-b border-border/20">
                      <td colSpan={8} className="py-2 px-0 pb-3">
                        <div className="bg-muted/20 rounded px-3 py-2 text-[11px] text-muted-foreground leading-relaxed border-l-2 border-primary/40">
                          {explanation}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Mobile card list */}
      <div className="sm:hidden space-y-2">
        {trades.map(t => {
          const pl          = t.net_pl ?? 0
          const plColor     = pl >= 0 ? 'text-green-400' : 'text-red-400'
          const session     = sessionLabel(t.session_at_entry)
          const outcome     = outcomeLabel(t.close_reason)
          const explanation = explanations[t.id]
          const isExpanded  = expanded[t.id]
          return (
            <div key={t.id} className="bg-muted/30 rounded-lg p-3 text-xs space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="font-mono font-bold">{t.instrument.replace('_', '/')}</span>
                <span className={`font-mono font-bold tabular-nums ${plColor}`}>
                  {pl >= 0 ? '+' : '−'}${Math.abs(pl).toFixed(2)}
                </span>
              </div>
              <div className="flex items-center justify-between text-muted-foreground">
                <span className={`font-bold ${t.direction === 'LONG' ? 'text-green-400' : 'text-red-400'}`}>
                  {t.direction === 'LONG' ? '↑ Buy' : '↓ Sell'}
                </span>
                <span>{format(new Date(t.closed_at), 'MMM d, h:mm a')}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className={outcome.color}>{outcome.text}</span>
                <span className={session.color}>{session.text}</span>
              </div>
              {explanation && (
                <div>
                  <button
                    onClick={() => toggle(t.id)}
                    className="text-primary/70 text-[10px]"
                  >
                    {isExpanded ? '▲ hide analysis' : '▼ why this trade?'}
                  </button>
                  {isExpanded && (
                    <div className="mt-1.5 text-[11px] text-muted-foreground leading-relaxed border-l-2 border-primary/40 pl-2">
                      {explanation}
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </>
  )
}
