import { usePositionStore } from '../../store'

export function PositionsTable() {
  const { positions } = usePositionStore()

  if (!positions.length) return (
    <div className="bg-card rounded-lg p-4 border border-border">
      <h3 className="text-sm font-semibold mb-2">Open Positions</h3>
      <p className="text-muted-foreground text-xs py-4 text-center">No open positions</p>
    </div>
  )

  return (
    <div className="bg-card rounded-lg p-4 border border-border overflow-x-auto">
      <h3 className="text-sm font-semibold mb-3">Open Positions</h3>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-muted-foreground border-b border-border">
            {['Instrument','Dir','Units','Entry','Current','SL','TP','P&L'].map(h => (
              <th key={h} className="text-left pb-2 pr-4 font-normal">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {positions.map(p => {
            const plColor = (p.unrealized_pl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
            return (
              <tr key={p.id} className="border-b border-border/30 hover:bg-muted/20">
                <td className="py-2 pr-4 font-mono">{p.instrument.replace('_','/')}</td>
                <td className={`py-2 pr-4 font-bold ${p.direction === 'LONG' ? 'text-green-400' : 'text-red-400'}`}>{p.direction}</td>
                <td className="py-2 pr-4 font-mono">{p.units.toLocaleString()}</td>
                <td className="py-2 pr-4 font-mono">{(p.avg_entry_price ?? 0).toFixed(5)}</td>
                <td className="py-2 pr-4 font-mono">{p.current_price?.toFixed(5) ?? '—'}</td>
                <td className="py-2 pr-4 font-mono text-red-400">{p.stop_loss?.toFixed(5) ?? '—'}</td>
                <td className="py-2 pr-4 font-mono text-green-400">{p.take_profit?.toFixed(5) ?? '—'}</td>
                <td className={`py-2 font-mono font-bold ${plColor}`}>
                  {(p.unrealized_pl ?? 0) >= 0 ? '+' : ''}{(p.unrealized_pl ?? 0).toFixed(2)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}