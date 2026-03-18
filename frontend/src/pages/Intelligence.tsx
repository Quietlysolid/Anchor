import { useState } from 'react'
import { GlowCard } from '../components/ui/GlowCard'
import { AIBriefPanel } from '../components/panels/AIBriefPanel'
import { SignalFeed } from '../components/signals/SignalFeed'
import { EconomicCalendar } from '../components/panels/EconomicCalendar'
import { useSignalStore } from '../store'
import { useIntelligenceHistory, useCalendar } from '../api/hooks'

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

  const signals = useSignalStore(s => s.signals)

  const { data: briefHistory }  = useIntelligenceHistory(20)
  const { data: calendarData }  = useCalendar()
  const briefs     = briefHistory ?? []
  const calEvents  = calendarData ?? []

  const filteredBriefs = search
    ? briefs.filter(b => b.content.toLowerCase().includes(search.toLowerCase()))
    : briefs

  function toggleExpand(i: number) {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(i)) {
        next.delete(i)
      } else {
        next.add(i)
      }
      return next
    })
  }

  return (
    <div className="min-h-screen bg-anchor-void p-5">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 lg:min-h-[calc(100vh-40px)]">

        {/* Left: Signal Feed */}
        <GlowCard padding={false} className="p-5 flex flex-col min-h-[360px] lg:overflow-hidden lg:max-h-[calc(100vh-60px)]">
          <SignalFeed signals={signals} />
        </GlowCard>

        {/* Right: AI Journal + Calendar stacked */}
        <div className="flex flex-col gap-4 min-h-[360px] lg:overflow-hidden lg:max-h-[calc(100vh-60px)]">

          {/* AI Journal — takes available space */}
          <GlowCard padding={false} className="p-5 flex flex-col flex-1 min-h-0 overflow-hidden">
            <div className="flex items-center justify-between mb-4 gap-3 shrink-0">
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
                <p className="text-sm text-anchor-muted text-center py-12">
                  {briefs.length === 0 ? 'No briefs generated yet.' : 'No results.'}
                </p>
              ) : (
                filteredBriefs.map((b, i) => {
                  const isOpen = expanded.has(i)
                  const badge  = BADGE[b.type.toUpperCase()] ?? { label: b.type, color: 'text-anchor-muted' }
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

          {/* Upcoming Events */}
          <GlowCard padding={false} className="p-5 shrink-0">
            <h2 className="text-sm font-semibold text-anchor-text mb-4">Upcoming Events</h2>
            <EconomicCalendar events={calEvents} />
          </GlowCard>
        </div>
      </div>
    </div>
  )
}
