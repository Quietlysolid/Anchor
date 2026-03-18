import { useState, useEffect } from 'react'
import { getPhaseInfo, fmtMins } from '../../utils/session'

export function SessionTimeline() {
  const [now, setNow] = useState(new Date())

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30_000)
    return () => clearInterval(t)
  }, [])

  const utcMins = now.getUTCHours() * 60 + now.getUTCMinutes()
  const { phase, label, remaining, nextLabel, nextIn } = getPhaseInfo(utcMins)

  const etTime = now.toLocaleTimeString('en-US', {
    timeZone: 'America/New_York',
    hour: 'numeric', minute: '2-digit', hour12: true,
  })

  const isActive = phase === 'london' || phase === 'lcr'

  const dotColor =
    phase === 'london' ? 'bg-amber-400' :
    phase === 'lcr'    ? 'bg-sky-400'   :
    phase === 'pre' || phase === 'post' ? 'bg-anchor-muted' :
    'bg-anchor-border'

  const labelColor =
    phase === 'london' ? 'text-amber-400' :
    phase === 'lcr'    ? 'text-sky-400'   :
    'text-anchor-muted'

  return (
    <div className="flex items-center gap-3 text-[11px] font-mono tracking-wide select-none">
      <span className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${dotColor} ${isActive ? 'animate-glow-pulse' : ''}`} />
      <span className={`font-semibold ${labelColor}`}>{label.toUpperCase()}</span>

      {remaining !== null && (
        <>
          <span className="text-anchor-border/50">·</span>
          <span className="text-anchor-muted">{fmtMins(remaining)} remaining</span>
        </>
      )}

      {nextIn > 0 && (
        <>
          <span className="text-anchor-border/50">·</span>
          <span className={isActive ? 'text-anchor-muted/40' : 'text-anchor-muted'}>
            {nextLabel} in {fmtMins(nextIn)}
          </span>
        </>
      )}

      <span className="ml-auto text-anchor-muted/40">{etTime} ET</span>
    </div>
  )
}
