import { useSystemHealth } from '../../api/hooks'
import { useSystemStore } from '../../store'

function Dot({ ok }: { ok: boolean }) {
  return <span className={`inline-block w-2 h-2 rounded-full ${ok ? 'bg-green-500' : 'bg-red-500'}`} />
}

function Row({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="flex justify-between items-center py-1.5 border-b border-border/40 last:border-0">
      <span className="text-muted-foreground text-xs">{label}</span>
      <span className="flex items-center gap-1.5 text-xs font-mono">
        {ok !== undefined && <Dot ok={ok} />}
        {value}
      </span>
    </div>
  )
}

export function SystemHealthPanel() {
  const { data } = useSystemHealth()
  const { lastHeartbeat, wsConnected } = useSystemStore()

  const heartbeatAge = lastHeartbeat ? Math.floor((Date.now() - lastHeartbeat.getTime()) / 1000) : null
  const hbOk = heartbeatAge !== null && heartbeatAge < 90

  return (
    <div className="bg-card rounded-lg p-4 border border-border">
      <h3 className="text-sm font-semibold mb-3 text-foreground">System Health</h3>
      <Row label="Dashboard feed"  value={wsConnected ? 'Connected' : 'Disconnected'} ok={wsConnected} />
      <Row label="Last ping"       value={heartbeatAge !== null ? heartbeatAge < 5 ? 'Just now' : heartbeatAge < 60 ? `${heartbeatAge}s ago` : `${Math.round(heartbeatAge / 60)}m ago` : 'Unknown'} ok={hbOk} />
      <Row label="Price stream"    value={data?.stream_connected ? 'Live' : 'Offline'} ok={data?.stream_connected} />
      <Row label="Open positions"  value={String(data?.open_positions ?? '—')} />
      <Row label="Last sync"       value={data?.last_reconciliation ? new Date(data.last_reconciliation).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', hour12: true }) : 'Never'} />
    </div>
  )
}
