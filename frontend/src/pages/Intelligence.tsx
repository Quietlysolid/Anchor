import { useMemo } from 'react'
import { useCalendar, useIntelligenceBrief, useTodayActivity } from '../api/hooks'
import { useSystemStore } from '../store'

function timeUntil(isoStr: string): string {
  const diff = new Date(isoStr).getTime() - Date.now()
  if (diff <= 0) return 'now'
  const h = Math.floor(diff / 3_600_000)
  const m = Math.floor((diff % 3_600_000) / 60_000)
  if (h > 0) return `in ${h}h ${m}m`
  return `in ${m}m`
}

function sessionStatus(): { label: string; sub: string; active: boolean } {
  const utcHour = new Date().getUTCHours()
  if (utcHour >= 7 && utcHour < 12) {
    return { label: 'London session is active', sub: 'Bot is watching for trade setups', active: true }
  }
  if (utcHour >= 17 && utcHour < 20) {
    return { label: 'Evening trades are active', sub: 'Looking for reversal setups', active: true }
  }
  if (utcHour < 7) {
    const minsUntil = (7 - utcHour) * 60 - new Date().getUTCMinutes()
    const h = Math.floor(minsUntil / 60)
    const m = minsUntil % 60
    return { label: 'Market watching begins soon', sub: `London session starts in ${h}h ${m}m`, active: false }
  }
  if (utcHour >= 12 && utcHour < 17) {
    const minsUntil = (17 - utcHour) * 60 - new Date().getUTCMinutes()
    const h = Math.floor(minsUntil / 60)
    const m = minsUntil % 60
    return { label: 'Quiet period', sub: `Evening trades open in ${h}h ${m}m`, active: false }
  }
  return { label: 'Markets closed', sub: 'Bot resumes Monday', active: false }
}

export default function Activity() {
  const { data: calendarData } = useCalendar()
  const { data: brief }        = useIntelligenceBrief()
  const { data: todayData }    = useTodayActivity()
  const { wsConnected }        = useSystemStore()

  const events  = useMemo(() => (calendarData ?? []).slice(0, 5), [calendarData])
  const session = sessionStatus()

  const signalsToday = todayData?.signals_today ?? 0
  const tradesToday  = todayData?.trades_today  ?? 0

  return (
    <div className="min-h-screen bg-anchor-void px-4 pt-4 pb-24 md:pb-8 space-y-4">

      {/* Bot status */}
      <div className="bg-anchor-surface rounded-2xl p-5">
        <div className="flex items-center gap-3 mb-4">
          <div className={`w-2.5 h-2.5 rounded-full ${wsConnected ? 'bg-anchor-green animate-pulse' : 'bg-anchor-red'}`} />
          <p className="text-anchor-text font-semibold">
            {wsConnected ? 'Bot is running' : 'Bot is offline'}
          </p>
        </div>
        <p className="text-anchor-text text-sm font-medium">{session.label}</p>
        <p className="text-anchor-muted text-sm mt-0.5">{session.sub}</p>
      </div>

      {/* Today's snapshot */}
      <div className="bg-anchor-surface rounded-2xl p-5">
        <p className="text-anchor-muted text-xs mb-4">Today's Activity</p>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-anchor-text text-3xl font-semibold font-mono">{signalsToday}</p>
            <p className="text-anchor-muted text-xs mt-1">setups checked</p>
          </div>
          <div>
            <p className="text-anchor-text text-3xl font-semibold font-mono">{tradesToday}</p>
            <p className="text-anchor-muted text-xs mt-1">trades placed</p>
          </div>
        </div>
      </div>

      {/* Latest AI brief — plain text summary */}
      {brief?.content && (
        <div className="bg-anchor-surface rounded-2xl p-5">
          <p className="text-anchor-muted text-xs mb-3">Latest Summary</p>
          <p className="text-anchor-text text-sm leading-relaxed whitespace-pre-line">{brief.content}</p>
        </div>
      )}

      {/* Upcoming news */}
      {events.length > 0 && (
        <div className="bg-anchor-surface rounded-2xl p-5">
          <p className="text-anchor-muted text-xs mb-4">Upcoming News</p>
          <div className="space-y-3">
            {events.map((ev, i) => (
              <div key={i} className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-anchor-text text-sm font-medium truncate">{ev.event_name}</p>
                  <p className="text-anchor-muted text-xs mt-0.5">
                    {new Date(ev.event_time).toLocaleString('en-US', {
                      timeZone: 'America/New_York',
                      weekday: 'short', month: 'short', day: 'numeric',
                      hour: 'numeric', minute: '2-digit', hour12: true,
                    })}
                  </p>
                </div>
                <p className="text-anchor-muted text-xs shrink-0 mt-0.5">
                  {timeUntil(ev.event_time)}
                </p>
              </div>
            ))}
          </div>
          <p className="text-anchor-muted/50 text-xs mt-4">
            The bot pauses briefly around high-impact events
          </p>
        </div>
      )}

    </div>
  )
}
