import { useSignalStore } from '../../store'

const THRESHOLD = 0.72

function ScoreBar({ label, score, weight }: { label: string; score: number | null; weight?: number }) {
  const pct = score !== null ? Math.round(score * 100) : 0
  const colorClass = pct >= 70 ? 'text-green-400' : pct >= 45 ? 'text-amber-400' : 'text-red-400'
  const barClass   = pct >= 70 ? 'bg-green-400'   : pct >= 45 ? 'bg-amber-400'   : 'bg-red-400'
  return (
    <div className="mb-2">
      <div className="flex justify-between text-xs mb-0.5">
        <span className="text-muted-foreground">
          {label}
          {weight !== undefined && <span className="text-muted-foreground/50 ml-1">{(weight * 100).toFixed(0)}%</span>}
        </span>
        <span className={colorClass}>{score !== null ? `${pct}%` : 'N/A'}</span>
      </div>
      <div className="h-1.5 bg-muted rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${barClass}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

export function SignalScoringPanel() {
  const { signals } = useSignalStore()
  const latest = signals[0]

  if (!latest) return (
    <div className="bg-card rounded-lg p-4 border border-border">
      <h3 className="text-sm font-semibold mb-3">Signal Scoring</h3>
      <p className="text-muted-foreground text-xs">Waiting for signals…</p>
    </div>
  )

  const dirColor = latest.direction === 'LONG' ? 'text-green-400' : 'text-red-400'
  const score    = latest.confluence_score ?? 0
  const passes   = score >= THRESHOLD
  const scoreColor = passes ? 'text-green-400' : score >= 0.60 ? 'text-amber-400' : 'text-red-400'
  const thresholdPct = Math.round(THRESHOLD * 100)

  return (
    <div className="bg-card rounded-lg p-4 border border-border">
      <div className="flex justify-between items-start mb-3">
        <h3 className="text-sm font-semibold">Signal Scoring</h3>
        <div className="text-right">
          <span className="text-xs text-muted-foreground">{latest.instrument}</span>
          <span className={`ml-2 text-xs font-bold ${dirColor}`}>{latest.direction}</span>
        </div>
      </div>

      {latest.suppressed && (
        <div className="text-xs text-amber-400 bg-amber-400/10 rounded px-2 py-1 mb-3">
          Suppressed: {latest.suppression_reason}
        </div>
      )}

      {/* Confluence meter */}
      <div className="mb-3">
        <div className="flex justify-between text-xs mb-1">
          <span className="text-muted-foreground">Confluence</span>
          <span className={`font-mono font-bold ${scoreColor}`}>
            {Math.round(score * 100)}%
            <span className="text-muted-foreground font-normal ml-1">/ {thresholdPct}% min</span>
          </span>
        </div>
        <div className="relative h-2 bg-muted rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${passes ? 'bg-green-400' : 'bg-amber-400'}`}
            style={{ width: `${Math.min(100, Math.round(score * 100))}%` }}
          />
          {/* Threshold marker */}
          <div
            className="absolute top-0 bottom-0 w-px bg-white/40"
            style={{ left: `${thresholdPct}%` }}
          />
        </div>
        {latest.ml_confidence != null && (
          <div className="text-xs text-muted-foreground mt-1">
            ML confidence: <span className="text-foreground font-mono">{Math.round(latest.ml_confidence * 100)}%</span>
          </div>
        )}
      </div>

      {/* Component scores */}
      <ScoreBar label="RSI Divergence"    score={latest.rsi_score}          weight={0.24} />
      <ScoreBar label="BB/KC Squeeze"     score={latest.bb_kc_score}        weight={0.20} />
      <ScoreBar label="ADX Filter"        score={latest.adx_score}          weight={0.15} />
      <ScoreBar label="Support/Resistance" score={latest.sr_score}          weight={0.24} />
      <ScoreBar label="Multi-Timeframe"   score={latest.mtf_score}          weight={0.04} />
      <ScoreBar label="Sentiment"         score={latest.csi_score}          weight={0.04} />
      <ScoreBar label="COT Positioning"   score={latest.cot_score}          weight={0.05} />
      <ScoreBar label="Rate Divergence"   score={latest.rate_divergence_score} weight={0.04} />

      <div className="mt-2 pt-2 border-t border-border text-xs text-muted-foreground flex gap-3">
        <span>Regime: <span className="text-foreground">{latest.regime_state ?? '—'}</span></span>
        <span>Session: <span className="text-foreground">{latest.session ?? '—'}</span></span>
        <span>TF: <span className="text-foreground">{latest.timeframe}</span></span>
      </div>
    </div>
  )
}
