import { useCalendar } from '../../api/hooks'
import { useWeightsStore } from '../../store'
import type { EconomicEvent } from '../../types'

// ET session windows (hours in ET, 24h)
// London: 3:15–8:00 AM ET  |  LCR: 1:00–4:00 PM ET
function getSessionTag(iso: string): string | null {
  const d = new Date(iso)
  const etStr = d.toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', hour12: false })
  const [hStr, mStr] = etStr.split(':')
  const mins = parseInt(hStr) * 60 + parseInt(mStr)
  if (mins >= 195 && mins < 480)  return 'London session'   // 3:15–8:00 AM
  if (mins >= 780 && mins < 960)  return 'LCR window'       // 1:00–4:00 PM
  return null
}

function timeLabel(iso: string, now: Date): string {
  const d    = new Date(iso)
  const diff = (d.getTime() - now.getTime()) / 1000 / 60  // minutes away

  const timeStr = d.toLocaleTimeString('en-US', {
    timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', hour12: true,
  })

  if (diff < 0)    return `${timeStr} (past)`
  if (diff < 60)   return `${timeStr} · in ${Math.round(diff)}m`
  if (diff < 180)  return `${timeStr} · in ${Math.floor(diff / 60)}h ${Math.round(diff % 60)}m`
  return timeStr
}

function isToday(iso: string): boolean {
  const d   = new Date(iso)
  const now = new Date()
  return d.toLocaleDateString('en-US', { timeZone: 'America/New_York' }) ===
         now.toLocaleDateString('en-US', { timeZone: 'America/New_York' })
}

interface GroupedEvent extends EconomicEvent { sessionTag: string | null }

export function EconomicCalendarPanel() {
  const { data: events, isLoading } = useCalendar()
  const instruments = useWeightsStore(s => s.instruments)
  const watched = new Set(instruments.flatMap(i => i.split('_')))
  const now = new Date()

  // Only show HIGH impact — MEDIUM doesn't trigger suppression
  const relevant: GroupedEvent[] = (events ?? [])
    .filter(e => watched.has(e.currency) && e.impact === 'HIGH')
    .slice(0, 8)
    .map(e => ({ ...e, sessionTag: getSessionTag(e.event_time) }))

  const todayEvents    = relevant.filter(e => isToday(e.event_time))
  const tomorrowEvents = relevant.filter(e => !isToday(e.event_time))

  const blockingToday = todayEvents.filter(e => e.sessionTag !== null)

  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <h3 className="text-sm font-semibold">Upcoming News</h3>
      <p className="text-[11px] text-muted-foreground/60 mt-0.5 mb-3 leading-relaxed">
        HIGH impact events pause trading for that session window
      </p>

      {isLoading && <p className="text-xs text-muted-foreground">Loading...</p>}

      {!isLoading && relevant.length === 0 && (
        <div className="py-3 text-center">
          <p className="text-xs text-green-400/70">✓ No high-impact events in the next 48h</p>
          <p className="text-[10px] text-muted-foreground/40 mt-1">All sessions should run normally</p>
        </div>
      )}

      {!isLoading && relevant.length > 0 && (
        <div className="space-y-3">

          {/* Warning if any events fall inside a session today */}
          {blockingToday.length > 0 && (
            <div className="bg-amber-400/8 border border-amber-400/20 rounded-lg px-2.5 py-2 text-[11px] text-amber-300">
              ⚠ {blockingToday.length === 1
                ? `1 event today will pause the ${blockingToday[0].sessionTag}`
                : `${blockingToday.length} events today fall inside active sessions`
              }
            </div>
          )}

          {/* Today */}
          {todayEvents.length > 0 && (
            <div>
              <p className="text-[10px] text-muted-foreground/50 uppercase tracking-wider mb-1.5">Today</p>
              <div className="space-y-2">
                {todayEvents.map((e, i) => (
                  <EventRow key={i} event={e} now={now} />
                ))}
              </div>
            </div>
          )}

          {/* Tomorrow */}
          {tomorrowEvents.length > 0 && (
            <div>
              <p className="text-[10px] text-muted-foreground/50 uppercase tracking-wider mb-1.5">Tomorrow</p>
              <div className="space-y-2">
                {tomorrowEvents.map((e, i) => (
                  <EventRow key={i} event={e} now={now} />
                ))}
              </div>
            </div>
          )}

        </div>
      )}
    </div>
  )
}

function EventRow({ event: e, now }: { event: GroupedEvent; now: Date }) {
  return (
    <div className="flex items-start gap-2 text-xs">
      <div className="shrink-0 w-24">
        <div className="text-muted-foreground font-mono text-[11px] leading-tight">
          {timeLabel(e.event_time, now)}
        </div>
        {e.sessionTag && (
          <div className="text-[10px] text-amber-400/70 mt-0.5">{e.sessionTag}</div>
        )}
      </div>
      <span className="shrink-0 mt-0.5 px-1.5 py-0.5 rounded border text-[10px] font-bold text-red-400 bg-red-400/10 border-red-400/30">
        {e.currency}
      </span>
      <span className="text-foreground leading-tight" title={e.event_name}>
        {e.event_name}
      </span>
    </div>
  )
}
