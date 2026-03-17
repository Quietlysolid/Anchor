import { useEdgeConfidence } from '../../api/hooks'

const CONFIG = {
  HIGH:    { label: 'HIGH',    dot: 'bg-green-500',  text: 'text-green-400',  bg: 'bg-green-400/10',  border: 'border-green-400/20',  desc: 'All signals normal. Edge expected to hold.' },
  REDUCED: { label: 'REDUCED', dot: 'bg-amber-400',  text: 'text-amber-400',  bg: 'bg-amber-400/10',  border: 'border-amber-400/20',  desc: 'One signal flagged. Monitor closely.' },
  LOW:     { label: 'LOW',     dot: 'bg-red-500',    text: 'text-red-400',    bg: 'bg-red-400/10',    border: 'border-red-400/20',    desc: 'Manual review recommended before trading.' },
}

const SIGNAL_LABELS: Record<string, string> = {
  session:     'Session character',
  correlation: 'Pair correlation',
  macro:       'Macro stress',
}

export function EdgeConfidencePanel() {
  const { data } = useEdgeConfidence()

  const conf   = data?.confidence ?? null
  const cfg    = conf ? CONFIG[conf] : null
  const checkedAt = data?.assessed_at
    ? new Date(data.assessed_at).toLocaleDateString('en-US', {
        timeZone: 'America/New_York', month: 'short', day: 'numeric',
        hour: 'numeric', minute: '2-digit',
      })
    : null

  return (
    <div className={`bg-card rounded-lg p-4 border ${cfg ? cfg.border : 'border-border'}`}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-foreground">Edge Confidence</h3>
        {conf && cfg && (
          <span className={`flex items-center gap-1.5 text-xs font-bold px-2 py-0.5 rounded-full ${cfg.text} ${cfg.bg}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot} ${conf !== 'HIGH' ? 'animate-pulse' : ''}`} />
            {cfg.label}
          </span>
        )}
        {!conf && (
          <span className="text-xs text-muted-foreground px-2 py-0.5 rounded-full bg-muted">—</span>
        )}
      </div>

      {conf && cfg ? (
        <>
          <p className="text-[11px] text-muted-foreground/70 leading-relaxed mb-3">{cfg.desc}</p>

          {/* Per-signal rows */}
          {data?.signals && (
            <div className="space-y-1.5">
              {Object.entries(data.signals).map(([key, sig]) => {
                if (!sig) return null
                return (
                  <div key={key} className="flex items-start gap-2 text-xs">
                    <span className={`mt-0.5 w-1.5 h-1.5 rounded-full shrink-0 ${sig.flagged ? 'bg-red-400' : 'bg-green-500'}`} />
                    <div className="flex-1 min-w-0">
                      <span className="font-medium text-foreground/80">{SIGNAL_LABELS[key] ?? key}</span>
                      {sig.flagged && (
                        <p className="text-[10px] text-red-400/80 leading-relaxed mt-0.5 break-words">{sig.reason}</p>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {checkedAt && (
            <p className="text-[10px] text-muted-foreground/40 mt-3">Assessed {checkedAt} ET</p>
          )}
        </>
      ) : (
        <div className="text-center py-3">
          <p className="text-xs text-muted-foreground">Not yet assessed</p>
          <p className="text-[10px] text-muted-foreground/50 mt-1 leading-relaxed">
            Runs daily after London close. First result tomorrow.
          </p>
        </div>
      )}
    </div>
  )
}
