import { useState } from 'react'
import { GlowCard } from '../components/ui/GlowCard'
import { AIBriefPanel } from '../components/panels/AIBriefPanel'
import { SignalFeed } from '../components/signals/SignalFeed'
import { useSignalStore } from '../store'
import { MOCK_SIGNALS } from '../mock/data'
import type { Signal } from '../types'

const BRIEFS = [
  {
    id: 'b1',
    type: 'POST' as const,
    created_at: new Date(Date.now() - 4 * 3600_000).toISOString(),
    content: `**Post-Session Analysis — London Close**\n\nStrong session. EUR/USD and GBP/USD both hit TP on London breakout signals. NZD/USD short entered at range extreme, currently holding. Total realised: +$31.20. Avg confluence score today: 0.81. System performed within expected parameters.\n\n**Tomorrow:** Watch CPI data at 08:30 ET — reduce sizing 50% until post-release.`,
  },
  {
    id: 'b2',
    type: 'PRE' as const,
    created_at: new Date(Date.now() - 18 * 3600_000).toISOString(),
    content: `**Pre-Session Analysis — London Open**\n\nDXY extended yesterday's rally by 0.4%. EUR/USD approaching structural resistance at 1.0862. GBP/USD range contracted overnight — BB/KC squeeze imminent.\n\n**Pair rankings today:** EUR/USD > EUR/JPY > NZD/USD > GBP/USD\n\nBias: mildly USD-bullish into CPI. Reduce JPY exposure — BOJ intervention risk elevated.`,
  },
  {
    id: 'b3',
    type: 'DRAWDOWN' as const,
    created_at: new Date(Date.now() - 2 * 86400_000).toISOString(),
    content: `**Drawdown Alert — 8.4% from peak**\n\nSystem has entered the 8% drawdown reduce threshold. All new position sizes halved until equity recovers to previous high-water mark.\n\nContext: 3 consecutive London sessions with below-average confluence. Regime has shifted from TRENDING to RANGING on EUR/USD and GBP/USD.\n\n**Recommended action:** Continue trading at 50% size. No parameter changes needed.`,
  },
]

const BADGE: Record<string, { label: string; color: string }> = {
  PRE:         { label: 'Pre-Session',  color: 'text-anchor-green' },
  POST:        { label: 'Post-Session', color: 'text-anchor-green' },
  DRAWDOWN:    { label: 'Drawdown',     color: 'text-anchor-red'   },
  WEEKLY:      { label: 'Weekly',       color: 'text-anchor-muted' },
  PRESESSION:  { label: 'Pre-Session',  color: 'text-anchor-green' },
  POSTSESSION: { label: 'Post-Session', color: 'text-anchor-green' },
}

export default function Intelligence() {
  const [search,   setSearch]   = useState('')
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const storeSignals = useSignalStore(s => s.signals)
  const signals: Signal[] = storeSignals.length ? storeSignals : MOCK_SIGNALS

  const filteredBriefs = search
    ? BRIEFS.filter(b => b.content.toLowerCase().includes(search.toLowerCase()))
    : BRIEFS

  function toggleExpand(i: number) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(i) ? next.delete(i) : next.add(i)
      return next
    })
  }

  return (
    <div className="min-h-screen bg-anchor-void p-5">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4" style={{ minHeight: 'calc(100vh - 40px)' }}>

        {/* Signal Feed */}
        <GlowCard padding={false} className="p-5 flex flex-col overflow-hidden" style={{ maxHeight: 'calc(100vh - 60px)' }}>
          <SignalFeed signals={signals} />
        </GlowCard>

        {/* AI Journal */}
        <GlowCard padding={false} className="p-5 flex flex-col overflow-hidden" style={{ maxHeight: 'calc(100vh - 60px)' }}>
          <div className="flex items-center justify-between mb-4 gap-3">
            <h2 className="text-sm font-semibold text-anchor-text">AI Journal</h2>
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search..."
              className="text-xs bg-anchor-void border border-anchor-border text-anchor-text placeholder-anchor-muted rounded-lg px-3 py-1.5 font-mono w-32 focus:outline-none focus:border-anchor-green/50 transition-colors"
            />
          </div>

          <div className="flex-1 overflow-y-auto space-y-2 pr-0.5">
            {filteredBriefs.length === 0 ? (
              <p className="text-sm text-anchor-muted text-center py-12">No results.</p>
            ) : (
              filteredBriefs.map((b, i) => {
                const isOpen = expanded.has(i)
                const badge  = BADGE[b.type.toUpperCase()] ?? BADGE['PRE']
                const ts     = new Date(b.created_at).toLocaleString('en-US', {
                  timeZone: 'America/New_York',
                  month: 'short', day: 'numeric',
                  hour: 'numeric', minute: '2-digit', hour12: true,
                })
                const preview = b.content.replace(/\*\*/g, '').split('\n').filter(Boolean)[1] ?? ''

                return (
                  <div key={b.id} className="rounded-xl border border-anchor-border overflow-hidden">
                    <button
                      type="button"
                      className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-white/[0.03] transition-colors"
                      onClick={() => toggleExpand(i)}
                    >
                      <span className={`text-xs font-semibold ${badge.color} shrink-0`}>{badge.label}</span>
                      <span className="text-xs text-anchor-muted font-mono">{ts}</span>
                      <svg
                        width="12" height="12" viewBox="0 0 12 12" fill="none"
                        stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
                        className={`ml-auto text-anchor-muted transition-transform duration-200 shrink-0 ${isOpen ? 'rotate-180' : ''}`}
                      >
                        <path d="M2 4l4 4 4-4"/>
                      </svg>
                    </button>

                    {!isOpen && preview && (
                      <p className="px-4 pb-3 text-xs text-anchor-muted truncate">{preview}</p>
                    )}

                    {isOpen && (
                      <div className="border-t border-anchor-border px-4 py-4">
                        <AIBriefPanel content={b.content} sessionType={b.type} typewrite={false} />
                      </div>
                    )}
                  </div>
                )
              })
            )}
          </div>
        </GlowCard>
      </div>
    </div>
  )
}
