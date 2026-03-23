import type { RolloutConfig } from '../../types'

interface Props {
  config: RolloutConfig | null | undefined
}

function formatPair(raw: string) {
  // "EUR_USD" → "EUR/USD"
  return raw.replace('_', '/')
}

export function RolloutCard({ config }: Props) {
  if (!config) return null

  const liveEngines: string[] = []
  const pausedEngines: string[] = []

  if (config.trend.enabled)          liveEngines.push('Trend following')
  else                                pausedEngines.push('Trend following')
  if (config.mean_reversion.enabled) liveEngines.push('Mean reversion')
  else                                pausedEngines.push('Mean reversion')
  if (config.lcr.enabled)            liveEngines.push('London Close Reversal')
  else                                pausedEngines.push('London Close Reversal')

  const pairs = config.instruments.filter(i => i !== 'GBP_USD')

  return (
    <div className="rounded-2xl bg-anchor-surface p-5 space-y-4">
      <p className="text-anchor-muted text-xs">What's trading right now</p>

      {/* Active strategies */}
      {liveEngines.length > 0 ? (
        <div className="space-y-2">
          {liveEngines.map(name => (
            <div key={name} className="flex items-center gap-2.5">
              <div className="w-2 h-2 rounded-full bg-anchor-green shrink-0" />
              <p className="text-anchor-text text-sm font-medium">{name}</p>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-anchor-muted text-sm">No strategies are active.</p>
      )}

      {/* Pairs */}
      {pairs.length > 0 && (
        <div>
          <p className="text-anchor-muted text-xs mb-2">Trading pairs</p>
          <div className="flex flex-wrap gap-2">
            {pairs.map(pair => (
              <span
                key={pair}
                className="text-xs font-mono bg-anchor-void/60 border border-anchor-border/50 rounded px-2 py-1 text-anchor-text"
              >
                {formatPair(pair)}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Paused */}
      {pausedEngines.length > 0 && (
        <div className="border-t border-anchor-border/40 pt-3">
          <p className="text-anchor-muted text-xs">
            Paused: {pausedEngines.join(', ')}
          </p>
        </div>
      )}
    </div>
  )
}
