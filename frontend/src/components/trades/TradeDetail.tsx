import { Sparkles } from 'lucide-react'
import type { Trade } from '../../types'

interface Props {
  trade:       Trade
  explanation?: string
}

export function TradeDetail({ trade, explanation }: Props) {
  const won = trade.net_pl >= 0
  const pipFactor = trade.instrument.includes('JPY') ? 100 : 10000
  const pips = ((trade.exit_price - trade.entry_price) * (trade.direction === 'LONG' ? 1 : -1) * pipFactor)
  const pipSign = pips >= 0 ? '+' : ''

  return (
    <div className="px-4 py-4 bg-anchor-surface/40 border-t border-anchor-border">

      {/* AI Analysis */}
      {explanation ? (
        <div className="mb-4">
          <div className="flex items-center gap-1.5 mb-2">
            <Sparkles size={11} className="text-anchor-green/70" />
            <p className="text-[11px] font-mono text-anchor-muted uppercase tracking-wide">AI Analysis</p>
          </div>
          <p className="text-xs text-anchor-text/80 leading-relaxed">{explanation}</p>
        </div>
      ) : (
        <div className="mb-4">
          <div className="flex items-center gap-1.5 mb-2">
            <Sparkles size={11} className="text-anchor-border" />
            <p className="text-[11px] font-mono text-anchor-muted uppercase tracking-wide">AI Analysis</p>
          </div>
          <p className="text-xs text-anchor-muted italic">Analysis pending — generates within 30 minutes of trade close.</p>
        </div>
      )}

      {/* Metadata */}
      <div className="flex flex-wrap gap-x-5 gap-y-1.5 text-xs font-mono border-t border-anchor-border/40 pt-3">
        <span className="text-anchor-muted">Session: <span className="text-anchor-text">{trade.session_at_entry ?? '—'}</span></span>
        <span className="text-anchor-muted">Regime: <span className="text-anchor-text">{trade.regime_at_entry ?? '—'}</span></span>
        <span className="text-anchor-muted">Close: <span className="text-anchor-text">{trade.close_reason}</span></span>
        <span className="text-anchor-muted">Pips: <span className={won ? 'text-anchor-green' : 'text-anchor-red'}>{pipSign}{pips.toFixed(1)}</span></span>
      </div>
    </div>
  )
}
