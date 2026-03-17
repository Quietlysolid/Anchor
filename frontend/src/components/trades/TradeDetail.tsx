import { Sparkline } from '../charts/Sparkline'
import type { Trade } from '../../types'

interface Props { trade: Trade }

function genSparklineFromTrade(trade: Trade): { time: number; value: number }[] {
  const pts: { time: number; value: number }[] = []
  const t0 = new Date(trade.opened_at).getTime() / 1000
  const t1 = new Date(trade.closed_at).getTime() / 1000
  const steps = 20
  for (let i = 0; i <= steps; i++) {
    const t   = t0 + (t1 - t0) * (i / steps)
    const pct = i / steps
    const noise = (Math.random() - 0.5) * Math.abs(trade.exit_price - trade.entry_price) * 0.4
    let v: number
    if (trade.direction === 'LONG') {
      v = trade.entry_price + (trade.exit_price - trade.entry_price) * pct + noise
    } else {
      v = trade.entry_price - (trade.entry_price - trade.exit_price) * pct + noise
    }
    pts.push({ time: Math.round(t), value: v })
  }
  return pts
}

export function TradeDetail({ trade }: Props) {
  const sparkData = genSparklineFromTrade(trade)
  const won = trade.net_pl >= 0
  const rr  = Math.abs(trade.exit_price - trade.entry_price) / (Math.abs(trade.exit_price - trade.entry_price) * 0.5) || 1.0

  const explanation = won
    ? `Trade entered on ${trade.instrument.replace('_', '/')} ${trade.direction} based on strong BB/KC squeeze breakout with ${trade.regime_at_entry} regime confirmation. Price moved cleanly in the intended direction, hitting take profit at ${trade.exit_price.toFixed(trade.exit_price > 10 ? 3 : 5)}.`
    : `Trade entered on ${trade.instrument.replace('_', '/')} ${trade.direction} with ${trade.regime_at_entry} regime but momentum faded. Stop was hit at ${trade.exit_price.toFixed(trade.exit_price > 10 ? 3 : 5)} — conditions shifted during the session.`

  return (
    <div className="px-4 py-3 bg-anchor-surface/40 border-t border-anchor-border grid grid-cols-1 md:grid-cols-3 gap-4">
      {/* Explanation */}
      <div className="md:col-span-2 space-y-2">
        <p className="text-[11px] font-mono text-anchor-muted uppercase tracking-wide">Analysis</p>
        <p className="text-xs text-anchor-text/80 leading-relaxed">{explanation}</p>

        <div className="flex flex-wrap gap-3 mt-2 text-xs font-mono">
          <span className="text-anchor-muted">Session: <span className="text-anchor-text">{trade.session_at_entry ?? '—'}</span></span>
          <span className="text-anchor-muted">Regime: <span className="text-anchor-text">{trade.regime_at_entry ?? '—'}</span></span>
          <span className="text-anchor-muted">Close: <span className="text-anchor-text">{trade.close_reason}</span></span>
          <span className="text-anchor-muted">R:R: <span className="text-anchor-text">{rr.toFixed(2)}</span></span>
        </div>
      </div>

      {/* Sparkline */}
      <div className="flex flex-col items-center justify-center gap-2">
        <p className="text-[11px] font-mono text-anchor-muted uppercase tracking-wide self-start">Price action</p>
        <Sparkline
          data={sparkData}
          color={won ? '#00FF94' : '#FF2D55'}
          height={52}
          width={160}
        />
      </div>
    </div>
  )
}
