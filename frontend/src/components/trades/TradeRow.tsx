import { useState } from 'react'
import { ChevronDown, Sparkles } from 'lucide-react'
import { Pill } from '../ui/Pill'
import { TradeDetail } from './TradeDetail'
import type { Trade } from '../../types'
interface Props { trade: Trade; explanation?: string }

function holdTime(opened: string, closed: string): string {
  const mins = Math.round((new Date(closed).getTime() - new Date(opened).getTime()) / 60_000)
  if (mins < 60)  return `${mins}m`
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return m ? `${h}h ${m}m` : `${h}h`
}

export function TradeRow({ trade, explanation }: Props) {
  const [expanded, setExpanded] = useState(false)
  const won    = trade.net_pl >= 0
  const plSign = trade.net_pl >= 0 ? '+' : ''
  const openedLabel = new Date(trade.opened_at).toLocaleDateString('en-US', {
    timeZone: 'America/New_York',
    month: 'short',
    day: 'numeric',
  })

  return (
    <>
      <tr
        className="border-b border-anchor-border/40 hover:bg-anchor-surface/40 transition-colors cursor-pointer"
        onClick={() => setExpanded(v => !v)}
      >
        <td className="py-2.5 px-3 font-mono text-xs text-anchor-muted">
          {openedLabel}
        </td>
        <td className="py-2.5 px-3 font-mono font-medium text-anchor-text text-sm">
          {trade.instrument.replace('_', '/')}
        </td>
        <td className="py-2.5 px-3">
          <Pill label={trade.direction} variant={trade.direction === 'LONG' ? 'buy' : 'sell'} />
        </td>
        <td className="py-2.5 px-3 font-mono text-xs text-anchor-text/80 text-right hidden sm:table-cell">
          {trade.entry_price.toFixed(trade.entry_price > 10 ? 3 : 5)}
        </td>
        <td className="py-2.5 px-3 font-mono text-xs text-anchor-text/80 text-right hidden sm:table-cell">
          {trade.exit_price.toFixed(trade.exit_price > 10 ? 3 : 5)}
        </td>
        <td className={`py-2.5 px-3 font-mono text-xs font-medium text-right ${won ? 'text-anchor-green' : 'text-anchor-red'}`}>
          {plSign}${Math.abs(trade.net_pl).toFixed(2)}
        </td>
        <td className="py-2.5 px-3 font-mono text-xs text-anchor-muted text-right hidden md:table-cell">
          {holdTime(trade.opened_at, trade.closed_at)}
        </td>
        <td className="py-2.5 px-3 font-mono text-xs text-anchor-muted hidden lg:table-cell">
          {trade.regime_at_entry ?? '—'}
        </td>
        <td className="py-2.5 px-3 w-8">
          <div className="flex items-center gap-1 justify-end">
            {explanation && <Sparkles size={10} className="text-anchor-green/50 shrink-0" />}
            <ChevronDown
              size={14}
              className={`text-anchor-muted transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
            />
          </div>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={9} className="p-0">
            <TradeDetail trade={trade} explanation={explanation} />
          </td>
        </tr>
      )}
    </>
  )
}
