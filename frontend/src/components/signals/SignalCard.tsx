import { Pill } from '../ui/Pill'
import { RegimeBadge } from '../ui/RegimeBadge'
import { ConfidenceBar } from './ConfidenceBar'
import type { Signal } from '../../types'
import { formatDistanceToNow } from 'date-fns'

interface Props { signal: Signal; index: number }

export function SignalCard({ signal, index }: Props) {
  const pct = Math.round((signal.confluence_score ?? 0) * 100)
  const age = formatDistanceToNow(new Date(signal.created_at), { addSuffix: true })

  return (
    <div
      className="border border-anchor-border rounded-xl p-3.5 space-y-3 animate-slide-in-top"
      style={{ animationDelay: `${index * 30}ms` }}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-mono font-semibold text-anchor-text">
            {signal.instrument.replace('_', '/')}
          </span>
          <span className="text-xs text-anchor-muted font-mono">{signal.timeframe}</span>
        </div>
        <div className="flex items-center gap-2">
          <Pill label={signal.direction} variant={signal.direction === 'LONG' ? 'buy' : 'sell'} />
          {signal.suppressed
            ? <span className="text-[10px] text-anchor-muted font-mono">Filtered</span>
            : <span className="text-[10px] text-anchor-green font-mono">Executed</span>
          }
        </div>
      </div>

      {/* Confidence */}
      <div className="space-y-1.5">
        <ConfidenceBar value={signal.confluence_score ?? 0} />
        <div className="flex items-center justify-between">
          {signal.regime_state
            ? <RegimeBadge regime={signal.regime_state} />
            : <span />
          }
          <span className="text-xs font-mono text-anchor-muted">{pct}% · {age}</span>
        </div>
      </div>
    </div>
  )
}
