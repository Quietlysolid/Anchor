import { usePositions, usePendingOrders } from '../../api/hooks'

export function PositionsTable() {
  const { data: positions = [] } = usePositions()
  const { data: allOrders = [] } = usePendingOrders()
  const pending = allOrders.filter(o => ['PENDING', 'SUBMITTED', 'ACKNOWLEDGED'].includes(o.state))
  const dp = (inst: string) => inst.includes('JPY') ? 3 : 5

  return (
    <div className="space-y-4">
      {/* Open positions — filled trades with live P&L */}
      <div className="bg-card rounded-lg p-4 border border-border overflow-x-auto">
        <h3 className="text-sm font-semibold mb-3">Open Positions</h3>
        {!positions.length ? (
          <p className="text-muted-foreground text-xs py-4 text-center">No open positions</p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground border-b border-border">
                {['Instrument','Dir','Units','Entry','Current','SL','TP','P&L'].map(h => (
                  <th key={h} className="text-left pb-2 pr-4 font-normal whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map(p => {
                const plColor = (p.unrealized_pl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
                const d = dp(p.instrument)
                return (
                  <tr key={p.id} className="border-b border-border/30 hover:bg-muted/20">
                    <td className="py-2 pr-4 font-mono">{p.instrument.replace('_','/')}</td>
                    <td className={"py-2 pr-4 font-bold " + (p.direction === 'LONG' ? 'text-green-400' : 'text-red-400')}>{p.direction}</td>
                    <td className="py-2 pr-4 font-mono">{p.units.toLocaleString()}</td>
                    <td className="py-2 pr-4 font-mono">{(p.avg_entry_price ?? 0).toFixed(d)}</td>
                    <td className="py-2 pr-4 font-mono">{p.current_price?.toFixed(d) ?? '—'}</td>
                    <td className="py-2 pr-4 font-mono text-red-400">{p.stop_loss?.toFixed(d) ?? '—'}</td>
                    <td className="py-2 pr-4 font-mono text-green-400">{p.take_profit?.toFixed(d) ?? '—'}</td>
                    <td className={"py-2 font-mono font-bold " + plColor}>
                      {(p.unrealized_pl ?? 0) >= 0 ? '+' : ''}{(p.unrealized_pl ?? 0).toFixed(2)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Pending limit orders — placed but waiting for price to reach the limit */}
      {pending.length > 0 && (
        <div className="bg-card rounded-lg p-4 border border-border overflow-x-auto">
          <h3 className="text-sm font-semibold mb-3">
            Pending Orders
            <span className="ml-2 text-xs font-normal text-muted-foreground">limit orders awaiting fill</span>
          </h3>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground border-b border-border">
                {['Instrument','Dir','Units','SL','TP','Status'].map(h => (
                  <th key={h} className="text-left pb-2 pr-4 font-normal whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pending.map(o => {
                const d = dp(o.instrument)
                return (
                  <tr key={o.id} className="border-b border-border/30 hover:bg-muted/20">
                    <td className="py-2 pr-4 font-mono">{o.instrument.replace('_','/')}</td>
                    <td className={"py-2 pr-4 font-bold " + (o.direction === 'LONG' ? 'text-green-400' : 'text-red-400')}>{o.direction}</td>
                    <td className="py-2 pr-4 font-mono">{o.units.toLocaleString()}</td>
                    <td className="py-2 pr-4 font-mono text-red-400">{o.stop_loss?.toFixed(d) ?? '—'}</td>
                    <td className="py-2 pr-4 font-mono text-green-400">{o.take_profit?.toFixed(d) ?? '—'}</td>
                    <td className="py-2 text-amber-400/80 uppercase text-xs">{o.state}</td>
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
