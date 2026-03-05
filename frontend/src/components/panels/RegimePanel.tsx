import { useSystemStore } from '../../store'

const REGIME_COLOR: Record<string, string> = {
  RANGING:  'text-blue-400  bg-blue-400/10  border-blue-400/30',
  TRENDING: 'text-green-400 bg-green-400/10 border-green-400/30',
  VOLATILE: 'text-red-400   bg-red-400/10   border-red-400/30',
}

export function RegimePanel() {
  const { currentRegime } = useSystemStore()
  const entries = Object.entries(currentRegime)

  return (
    <div className="bg-card rounded-lg p-4 border border-border">
      <h3 className="text-sm font-semibold mb-3">Market Regime (HMM)</h3>
      {entries.length === 0 ? (
        <p className="text-muted-foreground text-xs">Regime not yet detected</p>
      ) : (
        <div className="space-y-2">
          {entries.map(([instrument, { state, confidence }]) => (
            <div key={instrument} className="flex items-center justify-between">
              <span className="text-xs font-mono text-muted-foreground">{instrument.replace('_', '/')}</span>
              <div className="flex items-center gap-2">
                <div className={`text-xs px-2 py-0.5 rounded border font-medium ${REGIME_COLOR[state] ?? ''}`}>
                  {state}
                </div>
                <span className="text-xs text-muted-foreground font-mono">{((confidence ?? 0) * 100).toFixed(0)}%</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}