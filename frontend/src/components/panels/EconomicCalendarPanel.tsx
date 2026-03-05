import { useCalendar } from '../../api/hooks'

const IMPACT_COLORS: Record<string, string> = {
  HIGH:   'text-red-400 bg-red-400/10 border-red-400/30',
  MEDIUM: 'text-amber-400 bg-amber-400/10 border-amber-400/30',
}

// Currencies relevant to our 5 instruments
const WATCHED = new Set(['USD', 'EUR', 'GBP', 'JPY', 'AUD', 'CAD'])

function formatTime(iso: string) {
  const d = new Date(iso)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) +
    ' ' + d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

export function EconomicCalendarPanel() {
  const { data: events, isLoading } = useCalendar()

  const relevant = (events ?? []).filter(e => WATCHED.has(e.currency))

  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <h3 className="text-sm font-semibold mb-3">Upcoming News</h3>

      {isLoading && (
        <p className="text-xs text-muted-foreground">Loading...</p>
      )}

      {!isLoading && relevant.length === 0 && (
        <p className="text-xs text-muted-foreground">No high-impact events in next 48h</p>
      )}

      <div className="space-y-2">
        {relevant.slice(0, 8).map((e, i) => (
          <div key={i} className="flex items-start gap-2 text-xs">
            <span className="text-muted-foreground font-mono w-28 shrink-0">
              {formatTime(e.event_time)}
            </span>
            <span className={`shrink-0 px-1.5 py-0.5 rounded border text-[10px] font-bold ${IMPACT_COLORS[e.impact] ?? ''}`}>
              {e.currency}
            </span>
            <span className="text-foreground truncate" title={e.event_name}>
              {e.event_name}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
