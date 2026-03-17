import { useState } from 'react'
import { useIntelligenceBrief, useSessionQuality } from '../../api/hooks'

type ReportTab = 'PRESESSION' | 'POSTSESSION' | 'WEEKLY'

const TAB_LABELS: Record<ReportTab, string> = {
  PRESESSION:  'Pre-Session',
  POSTSESSION: 'Post-Session',
  WEEKLY:      'Weekly',
}

const ENV_CONFIG = {
  TRENDING: { label: 'TRENDING', color: 'text-green-400',  bg: 'bg-green-400/10',  border: 'border-green-400/20' },
  MIXED:    { label: 'MIXED',    color: 'text-amber-400',  bg: 'bg-amber-400/10',  border: 'border-amber-400/20' },
  CHOPPY:   { label: 'CHOPPY',  color: 'text-red-400',    bg: 'bg-red-400/10',    border: 'border-red-400/20' },
}

function fmtTime(iso: string) {
  return new Date(iso).toLocaleString('en-US', {
    timeZone: 'America/New_York',
    month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  }) + ' ET'
}

/**
 * Render the LLM brief content by splitting on **SECTION** markers.
 * Each section becomes a header + body block.
 */
function BriefContent({ content }: { content: string }) {
  // Split on bold markdown headers like **MACRO ENVIRONMENT**
  const parts = content.split(/(\*\*[A-Z][A-Z\s]+\*\*)/)

  return (
    <div className="space-y-3 text-[12px] leading-relaxed">
      {parts.map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          const title = part.slice(2, -2)
          return (
            <h4 key={i} className="text-[11px] font-semibold tracking-wide text-muted-foreground/70 uppercase mt-4 first:mt-0">
              {title}
            </h4>
          )
        }
        const trimmed = part.trim()
        if (!trimmed) return null
        // Render bullet lines
        const lines = trimmed.split('\n').filter(l => l.trim())
        return (
          <div key={i} className="space-y-1">
            {lines.map((line, j) => {
              const isBullet = line.trim().startsWith('•') || line.trim().startsWith('-')
              if (isBullet) {
                return (
                  <div key={j} className="flex gap-2 text-foreground/80">
                    <span className="text-muted-foreground/50 shrink-0">•</span>
                    <span>{line.replace(/^[\s•\-]+/, '')}</span>
                  </div>
                )
              }
              return <p key={j} className="text-foreground/80">{line}</p>
            })}
          </div>
        )
      })}
    </div>
  )
}

export function IntelligenceBriefPanel() {
  const [tab, setTab] = useState<ReportTab>('PRESESSION')
  const { data: brief, isLoading } = useIntelligenceBrief(tab)
  const { data: sq } = useSessionQuality()

  const env = sq?.environment ?? 'MIXED'
  const envCfg = ENV_CONFIG[env] ?? ENV_CONFIG.MIXED

  return (
    <div className="bg-card rounded-lg border border-border flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 pt-3 pb-2 border-b border-border/50">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold">AI Co-Pilot</h3>
          {sq && (
            <div className={`flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-semibold ${envCfg.color} ${envCfg.bg}`}>
              <span className={`inline-block w-1.5 h-1.5 rounded-full ${envCfg.color.replace('text-', 'bg-')}`} />
              {env}
            </div>
          )}
        </div>

        {/* Session quality scalars */}
        {sq && (
          <div className="flex items-center gap-3 text-[10px] text-muted-foreground/70">
            <span>Size <span className="font-mono text-foreground/80">{Math.round(sq.size_scale * 100)}%</span></span>
            <span>Threshold <span className="font-mono text-foreground/80">
              {sq.threshold_adjustment >= 0 ? '+' : ''}{(sq.threshold_adjustment * 100).toFixed(0)}%
            </span></span>
          </div>
        )}
      </div>

      {/* Pair rankings strip */}
      {sq?.pair_rankings && sq.pair_rankings.length > 0 && (
        <div className="px-4 py-1.5 border-b border-border/30 flex items-center gap-2">
          <span className="text-[10px] text-muted-foreground/50 shrink-0">Rankings</span>
          {sq.pair_rankings.map((p, i) => (
            <span key={p} className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
              i === 0 ? 'text-green-400 bg-green-400/10' :
              i <= 2 ? 'text-foreground/80 bg-muted' :
              'text-muted-foreground/50'
            }`}>
              {i + 1}. {p.replace('_', '/')}
            </span>
          ))}
        </div>
      )}

      {/* Tab bar */}
      <div className="flex gap-0.5 px-4 pt-2">
        {(Object.keys(TAB_LABELS) as ReportTab[]).map(t => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`px-2.5 py-1 text-[11px] rounded-md font-medium transition-all duration-100 ${
              tab === t
                ? 'bg-primary text-white'
                : 'text-muted-foreground hover:text-foreground hover:bg-muted'
            }`}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {/* Brief content */}
      <div className="flex-1 px-4 py-3 overflow-y-auto max-h-[420px]">
        {isLoading ? (
          <div className="space-y-2">
            {[...Array(4)].map((_, i) => (
              <div key={i} className={`skeleton h-3 rounded ${i === 0 ? 'w-32' : i % 2 === 0 ? 'w-full' : 'w-4/5'}`} />
            ))}
          </div>
        ) : brief ? (
          <>
            <div className="flex items-center justify-between mb-3">
              <span className="text-[10px] text-muted-foreground/50">
                Generated {fmtTime(brief.created_at)}
              </span>
              <span className="text-[10px] text-muted-foreground/40 font-mono">
                {brief.tokens_used.toLocaleString()} tokens
              </span>
            </div>
            <BriefContent content={brief.content} />
          </>
        ) : (
          <div className="flex flex-col items-center justify-center h-24 text-center">
            <p className="text-[12px] text-muted-foreground/60">No {TAB_LABELS[tab]} brief available yet.</p>
            <p className="text-[10px] text-muted-foreground/40 mt-1">
              {tab === 'PRESESSION' && 'Generated Mon–Fri at 06:30 UTC'}
              {tab === 'POSTSESSION' && 'Generated Mon–Fri at 12:30 UTC'}
              {tab === 'WEEKLY' && 'Generated Sunday at 22:00 UTC'}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
