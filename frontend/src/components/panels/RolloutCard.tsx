import type { EngineRolloutConfig, RolloutConfig } from '../../types'
import { Pill } from '../ui/Pill'
import { StatusDot } from '../ui/StatusDot'

interface Props {
  config: RolloutConfig | null | undefined
}

function formatRisk(riskPct: number) {
  return `${(riskPct * 100).toFixed(2)}%`
}

function sameList(a: string[], b: readonly string[]) {
  return a.length === b.length && a.every((value, index) => value === b[index])
}

function sameEngine(a: EngineRolloutConfig, b: { enabled: boolean; paper_only: boolean; risk_pct: number }) {
  return (
    a.enabled === b.enabled &&
    a.paper_only === b.paper_only &&
    Math.abs(a.risk_pct - b.risk_pct) < 1e-9
  )
}

function getDriftWarnings(config: RolloutConfig) {
  const warnings: string[] = []
  const expected = config.expected_pilot

  if (!sameList(config.instruments, expected.instruments)) {
    warnings.push(`Universe drift: ${config.instruments.join(', ')}`)
  }
  if (!sameEngine(config.trend, expected.trend)) {
    warnings.push('Trend rollout differs from pilot')
  }
  if (!sameEngine(config.mean_reversion, expected.mean_reversion)) {
    warnings.push('Mean reversion rollout differs from pilot')
  }
  if (!sameEngine(config.lcr, expected.lcr)) {
    warnings.push('LCR rollout differs from pilot')
  }
  if (!sameEngine(config.m15, expected.m15)) {
    warnings.push('M15 rollout differs from pilot')
  }

  return warnings
}

function EngineRow({
  label,
  config,
}: {
  label: string
  config: EngineRolloutConfig
}) {
  const live = config.enabled && !config.paper_only
  const paper = config.enabled && config.paper_only

  return (
    <div className="grid grid-cols-[auto_1fr_auto] items-center gap-3 rounded-lg border border-anchor-border/50 bg-anchor-void/40 px-3 py-2.5">
      <StatusDot connected={live} className="mt-0.5" />
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-anchor-text">{label}</span>
          {!config.enabled && <Pill label="OFF" variant="low" />}
          {paper && <Pill label="PAPER" variant="med" />}
          {live && <Pill label="LIVE" variant="buy" />}
        </div>
      </div>
      <span className="text-xs font-mono text-anchor-muted">{formatRisk(config.risk_pct)}</span>
    </div>
  )
}

export function RolloutCard({ config }: Props) {
  if (!config) {
    return (
      <div className="rounded-xl border border-anchor-border/60 bg-anchor-surface p-5">
        <h2 className="text-sm font-semibold text-anchor-text mb-3">Live Rollout</h2>
        <p className="text-sm text-anchor-muted">Rollout config unavailable.</p>
      </div>
    )
  }

  const driftWarnings = getDriftWarnings(config)
  const hasDrift = driftWarnings.length > 0

  return (
    <div className={`rounded-xl border p-5 ${hasDrift ? 'border-amber-400/40 bg-amber-400/5' : 'border-anchor-border/60 bg-anchor-surface'}`}>
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <h2 className="text-sm font-semibold text-anchor-text">Live Rollout</h2>
          <p className="text-xs text-anchor-muted mt-1">
            Active universe: {config.instruments.join(', ')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {hasDrift && <Pill label="DRIFT" variant="med" />}
          <Pill label={`Fallback ${formatRisk(config.max_risk_per_trade_fallback)}`} variant="muted" />
        </div>
      </div>

      {hasDrift && (
        <div className="mb-4 rounded-lg border border-amber-400/25 bg-amber-400/10 px-3 py-2.5">
          <p className="text-[10px] text-amber-300 tracking-[0.15em] uppercase mb-1">Pilot Drift</p>
          <div className="space-y-1">
            {driftWarnings.map((warning) => (
              <p key={warning} className="text-xs text-amber-100/90">
                {warning}
              </p>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-2.5 mb-4">
        <EngineRow label="Trend" config={config.trend} />
        <EngineRow label="Mean Reversion" config={config.mean_reversion} />
        <EngineRow label="LCR" config={config.lcr} />
        <EngineRow label="M15" config={config.m15} />
      </div>

      <div className="grid grid-cols-2 gap-3 border-t border-anchor-border/50 pt-3">
        <div>
          <p className="text-[10px] text-anchor-muted tracking-[0.15em] uppercase mb-1">Threshold</p>
          <p className="text-sm font-mono text-anchor-text">{config.min_confluence_score.toFixed(2)}</p>
        </div>
        <div>
          <p className="text-[10px] text-anchor-muted tracking-[0.15em] uppercase mb-1">ML Floor</p>
          <p className="text-sm font-mono text-anchor-text">{config.min_ml_confidence.toFixed(2)}</p>
        </div>
      </div>
    </div>
  )
}
