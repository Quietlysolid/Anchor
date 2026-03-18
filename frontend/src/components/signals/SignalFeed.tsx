import { useRef, useState } from 'react'
import { SignalCard } from './SignalCard'
import { getPhaseInfo, fmtMins } from '../../utils/session'
import type { Signal } from '../../types'

function emptyStateText(): string {
  const now     = new Date()
  const utcMins = now.getUTCHours() * 60 + now.getUTCMinutes()
  const { phase, nextLabel, nextIn } = getPhaseInfo(utcMins)
  if (phase === 'london') return 'London session active — signals will appear here.'
  if (phase === 'lcr')    return 'LCR window active — signals will appear here.'
  return `${nextLabel} opens in ${fmtMins(nextIn)}.`
}

interface Props { signals: Signal[] }

export function SignalFeed({ signals }: Props) {
  const [paused, setPaused] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-anchor-text">Signal Feed</h2>
        <div className="flex items-center gap-2">
          <span className="text-xs text-anchor-muted font-mono">{signals.length} signals</span>
          {paused && (
            <span className="text-[10px] text-amber-400 font-mono">paused</span>
          )}
        </div>
      </div>

      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto space-y-2 pr-1"
        onMouseEnter={() => setPaused(true)}
        onMouseLeave={() => setPaused(false)}
      >
        {signals.length === 0 ? (
          <div className="text-center py-12 text-anchor-muted text-sm font-mono">
            {emptyStateText()}
          </div>
        ) : (
          signals.map((s, i) => <SignalCard key={s.id} signal={s} index={i} />)
        )}
      </div>
    </div>
  )
}
