import { usePendingOrders } from '../../api/hooks'
import { usePositionStore, useMarketStore } from '../../store'
import type { Position } from '../../types'

// Compute live unrealized P&L from the tick stream price
// USD-quote pairs (EUR/USD, GBP/USD, NZD/USD, AUD/USD): P&L = sign × (mid − entry) × units
// USD-base pairs (USD/JPY, USD/CAD):                    P&L = sign × (mid − entry) × units / mid
function livePL(pos: Position, mid: number): number {
  const sign  = pos.direction === 'LONG' ? 1 : -1
  const entry = pos.avg_entry_price ?? 0
  const units = pos.units ?? 0
  if (pos.instrument.startsWith('USD_')) return sign * (mid - entry) * units / mid
  return sign * (mid - entry) * units
}

export function PositionsTable() {
  const positions = usePositionStore(s => s.positions)
  const prices    = useMarketStore(s => s.prices)
  const { data: allOrders = [] } = usePendingOrders()
  const pending = allOrders.filter(o => ['PENDING', 'SUBMITTED', 'ACKNOWLEDGED'].includes(o.state))
  const dp = (inst: string) => inst.includes('JPY') ? 3 : 5

  if (!positions.length && !pending.length) return null

  return (
    <div className="space-y-4">
      {/* Open positions with live P&L from tick stream */}
      <div className="bg-card rounded-lg p-4 border border-border overflow-x-auto">
        <h3 className="text-sm font-semibold mb-3">Open Positions</h3>
        {!positions.length ? (
          <div className="py-4 text-center">
            <p className="text-xs text-muted-foreground/50">No open positions</p>
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground border-b border-border">
                {['Pair','Direction','Units','Entry','Current','Stop loss','Take profit','P&L'].map(h => (
                  <th key={h} className="text-left pb-2 pr-4 font-normal whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map((p, i) => {
                const tick    = prices[p.instrument]
                const mid     = tick ? (tick.bid + tick.ask) / 2 : null
                const pl      = mid !== null ? livePL(p, mid) : (p.unrealized_pl ?? 0)
                const plColor = pl >= 0 ? 'text-green-400' : 'text-red-400'
                const d       = dp(p.instrument)
                return (
                  <tr key={p.oanda_trade_id ?? i} className="border-b border-border/30 hover:bg-muted/20">
                    <td className="py-2 pr-4 font-mono">{p.instrument.replace('_', '/')}</td>
                    <td className={'py-2 pr-4 font-bold ' + (p.direction === 'LONG' ? 'text-green-400' : 'text-red-400')}>
                      {p.direction === 'LONG' ? '↑ Buy' : '↓ Sell'}
                    </td>
                    <td className="py-2 pr-4 font-mono">{(p.units ?? 0).toLocaleString()}</td>
                    <td className="py-2 pr-4 font-mono">{(p.avg_entry_price ?? 0).toFixed(d)}</td>
                    <td className="py-2 pr-4 font-mono">{mid !== null ? mid.toFixed(d) : '—'}</td>
                    <td className="py-2 pr-4 font-mono text-red-400">{p.stop_loss?.toFixed(d) ?? '—'}</td>
                    <td className="py-2 pr-4 font-mono text-green-400">{p.take_profit?.toFixed(d) ?? '—'}</td>
                    <td className={'py-2 font-mono font-bold tabular-nums ' + plColor}>
                      {pl >= 0 ? '+' : ''}{pl.toFixed(2)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Pending limit orders */}
      {pending.length > 0 && (
        <div className="bg-card rounded-lg p-4 border border-border overflow-x-auto">
          <h3 className="text-sm font-semibold mb-3">
            Pending Orders
            <span className="ml-2 text-xs font-normal text-muted-foreground">limit orders awaiting fill</span>
          </h3>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground border-b border-border">
                {['Pair','Direction','Units','Stop loss','Take profit','Status'].map(h => (
                  <th key={h} className="text-left pb-2 pr-4 font-normal whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pending.map(o => {
                const d = dp(o.instrument)
                return (
                  <tr key={o.id} className="border-b border-border/30 hover:bg-muted/20">
                    <td className="py-2 pr-4 font-mono">{o.instrument.replace('_', '/')}</td>
                    <td className={'py-2 pr-4 font-bold ' + (o.direction === 'LONG' ? 'text-green-400' : 'text-red-400')}>
                      {o.direction === 'LONG' ? '↑ Buy' : '↓ Sell'}
                    </td>
                    <td className="py-2 pr-4 font-mono">{o.units.toLocaleString()}</td>
                    <td className="py-2 pr-4 font-mono text-red-400">{o.stop_loss?.toFixed(d) ?? '—'}</td>
                    <td className="py-2 pr-4 font-mono text-green-400">{o.take_profit?.toFixed(d) ?? '—'}</td>
                    <td className="py-2 text-amber-400/80 text-xs">
                      {({ PENDING: 'Waiting to fill', SUBMITTED: 'Sent to broker', ACKNOWLEDGED: 'Confirmed' } as Record<string, string>)[o.state] ?? o.state}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
