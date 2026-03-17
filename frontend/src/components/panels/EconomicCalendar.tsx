import { useMemo, useEffect, useRef, useState } from 'react'
import { Pill } from '../ui/Pill'
import type { EconomicEvent } from '../../types'

interface Props { events: EconomicEvent[] }

function Countdown({ targetTime }: { targetTime: string }) {
  const [secs, setSecs] = useState(() => Math.max(0, Math.round((new Date(targetTime).getTime() - Date.now()) / 1000)))
  const ref = useRef<ReturnType<typeof setInterval>>()

  useEffect(() => {
    ref.current = setInterval(() => {
      setSecs(s => Math.max(0, s - 1))
    }, 1000)
    return () => clearInterval(ref.current)
  }, [])

  if (secs <= 0) return <span className="text-anchor-red font-mono text-xs">NOW</span>

  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = secs % 60

  const fmt = h > 0
    ? `${h}h ${String(m).padStart(2, '0')}m`
    : `${m}:${String(s).padStart(2, '0')}`

  const urgent = secs < 3600
  return (
    <span className={`font-mono text-xs ${urgent ? 'text-amber-400' : 'text-anchor-muted'}`}>
      {fmt}
    </span>
  )
}

const CURRENCIES_BY_PAIR: Record<string, string[]> = {
  EUR_USD: ['EUR', 'USD'], GBP_USD: ['GBP', 'USD'],
  NZD_USD: ['NZD', 'USD'], USD_CAD: ['USD', 'CAD'],
  EUR_JPY: ['EUR', 'JPY'], AUD_USD: ['AUD', 'USD'],
}

export function EconomicCalendar({ events }: Props) {
  const upcoming = useMemo(() =>
    [...events]
      .filter(e => new Date(e.event_time).getTime() > Date.now() - 60_000)
      .sort((a, b) => new Date(a.event_time).getTime() - new Date(b.event_time).getTime())
      .slice(0, 4),
    [events]
  )

  if (!upcoming.length) {
    return (
      <div className="p-4 text-sm text-anchor-muted font-mono italic text-center">
        No events in the next 48h.
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {upcoming.map((ev, i) => {
        const relevant = Object.entries(CURRENCIES_BY_PAIR)
          .filter(([, ccys]) => ccys.includes(ev.currency))
          .map(([pair]) => pair.replace('_', '/'))
          .slice(0, 2)

        return (
          <div key={i} className="flex items-start gap-3 py-2 border-b border-anchor-border/40 last:border-0">
            <div className="flex flex-col items-center min-w-[52px]">
              <Countdown targetTime={ev.event_time} />
              <span className="text-[10px] text-anchor-muted font-mono mt-0.5">
                {new Date(ev.event_time).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', hour12: true })}
              </span>
            </div>

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5 mb-0.5">
                <Pill
                  label={ev.impact}
                  variant={ev.impact === 'HIGH' ? 'high' : 'med'}
                />
                <span className="text-[10px] font-mono text-anchor-muted">{ev.currency}</span>
              </div>
              <p className="text-xs text-anchor-text/80 truncate font-sans">{ev.event_name}</p>
              {(ev.forecast || ev.previous) && (
                <div className="flex gap-2 mt-0.5 text-[10px] text-anchor-muted font-mono">
                  {ev.forecast  && <span>F: {ev.forecast}</span>}
                  {ev.previous  && <span>P: {ev.previous}</span>}
                </div>
              )}
            </div>

            {relevant.length > 0 && (
              <div className="flex flex-col gap-0.5">
                {relevant.map(p => (
                  <span key={p} className="text-[9px] font-mono text-anchor-muted/60 bg-anchor-border/40 px-1 py-0.5 rounded">{p}</span>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
