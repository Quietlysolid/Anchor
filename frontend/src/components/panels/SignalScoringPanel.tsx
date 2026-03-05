import { useSignalStore } from '../../store'

function ScoreBar({ label, score }: { label: string; score: number | null }) {
  const pct = score !== null ? Math.round(score * 100) : 0
  const colorClass = pct >= 70 ? 'text-green-400' : pct >= 45 ? 'text-amber-400' : 'text-red-400'
  const barClass  = pct >= 70 ? 'bg-green-400'   : pct >= 45 ? 'bg-amber-400'   : 'bg-red-400'
  return (
    <div className="mb-2">
      <div className="flex justify-between text-xs mb-0.5">
        <span className="text-muted-foreground">{label}</span>
        <span className={colorClass}>{score !== null ? `${pct}%` : 'N/A'}</span>
      </div>
      <div className="h-1.5 bg-muted rounded-full overflow-hidden">
        <div className={`score-bar h-full rounded-full transition-all ${barClass}`} data-pct={pct} />
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

      <div className="text-xs text-muted-foreground mb-2">
        Confluence: <span className="text-foreground font-mono">{((latest.confluence_score ?? 0) * 100).toFixed(0)}%</span>
        {latest.ml_confidence && <span className="ml-3">ML: <span className="text-foreground font-mono">{((latest.ml_confidence ?? 0) * 100).toFixed(0)}%</span></span>}
      </div>

      <ScoreBar label="RSI Divergence"    score={latest.rsi_score} />
      <ScoreBar label="BB/KC Squeeze"     score={latest.bb_kc_score} />
      <ScoreBar label="ADX Filter"        score={latest.adx_score} />
      <ScoreBar label="Support/Resistance" score={latest.sr_score} />
      <ScoreBar label="Multi-Timeframe"   score={latest.mtf_score} />
      <ScoreBar label="Currency Strength" score={latest.csi_score} />

      <div className="mt-2 text-xs text-muted-foreground">
        Regime: <span className="text-foreground">{latest.regime_state ?? '—'}</span>
        <span className="ml-3">Session: <span className="text-foreground">{latest.session ?? '—'}</span></span>
      </div>
    </div>
  )
}