import { useSystemStore, useWeightsStore } from '../../store'

const REGIME_COLOR: Record<string, string> = {
  RANGING:  'text-blue-400  bg-blue-400/10  border-blue-400/30',
  TRENDING: 'text-green-400 bg-green-400/10 border-green-400/30',
  VOLATILE: 'text-red-400   bg-red-400/10   border-red-400/30',
}

const REGIME_BAR: Record<string, string> = {
  RANGING:  'bg-blue-400',
  TRENDING: 'bg-green-400',
  VOLATILE: 'bg-red-400',
}

const REGIME_LABEL: Record<string, string> = {
  TRENDING: 'Trending',
  RANGING:  'Ranging',
  VOLATILE: 'Volatile',
}

const REGIME_NOTE: Record<string, string> = {
  TRENDING: 'Moving in a clear direction — morning trend strategy is active',
  RANGING:  'Bouncing sideways in a range — morning trades are skipped, evening reversals still run',
  VOLATILE: 'Fast, unpredictable moves — system sits out until it calms down',
}

const REGIME_TRADING: Record<string, string> = {
  TRENDING: 'Both strategies can trade',
  RANGING:  'Morning trades skipped',
  VOLATILE: 'Trading paused',
}

export function RegimePanel() {
  const { currentRegime } = useSystemStore()
  const instruments = useWeightsStore(s => s.instruments)
  const entries = Object.entries(currentRegime)

  return (
    <div className="bg-card rounded-lg p-4 border border-border">
      <h3 className="text-sm font-semibold">Market Condition</h3>
      <p className="text-[11px] text-muted-foreground/60 mt-0.5 mb-3">
        An AI model watches each pair's price behaviour and classifies it. This determines which strategies are allowed to trade.
      </p>
      {entries.length === 0 ? (
        <div className="space-y-2.5">
          {instruments.map(i => i.replace('_', '/')).map(pair => (
            <div key={pair} className="space-y-1">
              <div className="flex justify-between items-center">
                <span className="text-xs font-mono text-muted-foreground">{pair}</span>
                <div className="skeleton h-4 w-16 rounded-full" />
              </div>
              <div className="skeleton h-1 w-full" />
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          {entries.map(([instrument, { state, confidence }]) => {
            const confPct = Math.round((confidence ?? 0) * 100)
            const label = REGIME_LABEL[state] ?? state
            const note  = REGIME_NOTE[state]
            return (
              <div key={instrument} className="space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono text-muted-foreground">{instrument.replace('_', '/')}</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${REGIME_COLOR[state] ?? ''}`}>
                    {label}
                  </span>
                </div>
                <div className="h-1 bg-muted rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${REGIME_BAR[state] ?? 'bg-muted-foreground'}`}
                    style={{ width: `${confPct}%` }}
                  />
                </div>
                <div className="flex justify-between items-start gap-2">
                  {note && <div className="text-[10px] text-muted-foreground/50 leading-tight">{note}</div>}
                  <div className="text-[10px] text-muted-foreground/40 shrink-0">{confPct}% sure</div>
                </div>
                {REGIME_TRADING[state] && (
                  <div className={`text-[10px] font-medium mt-0.5 ${
                    state === 'TRENDING' ? 'text-green-400/60' :
                    state === 'RANGING'  ? 'text-amber-400/60' : 'text-red-400/60'
                  }`}>
                    → {REGIME_TRADING[state]}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
