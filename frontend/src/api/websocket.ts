import type { WsMessage, WsChannel } from '../types'

type Handler = (data: unknown) => void
type StatusHandler = (connected: boolean) => void

class AnchorWebSocket {
  private ws: WebSocket | null = null
  private handlers: Map<WsChannel, Set<Handler>> = new Map()
  private statusHandlers: Set<StatusHandler> = new Set()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private intentionalClose = false
  private attempt = 0

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    this.ws = new WebSocket(`${proto}://${location.host}/ws`)

    this.ws.onopen = () => {
      console.info('[WS] connected')
      this.attempt = 0
      this.notifyStatus(true)
      this.subscribe(['positions', 'orders', 'heartbeat', 'account', 'events'])
    }

    this.ws.onmessage = (evt) => {
      try {
        const msg: WsMessage = JSON.parse(evt.data)
        this.handlers.get(msg.channel)?.forEach(h => h(msg.data))
      } catch { /* ignore malformed */ }
    }

    this.ws.onclose = () => {
      this.notifyStatus(false)
      if (!this.intentionalClose) {
        // exponential backoff: 2s, 4s, 8s … capped at 30s
        const delay = Math.min(2000 * 2 ** this.attempt, 30_000)
        this.attempt++
        console.warn(`[WS] disconnected — reconnecting in ${delay / 1000}s`)
        this.reconnectTimer = setTimeout(() => this.connect(), delay)
      }
    }
  }

  private subscribe(channels: WsChannel[]) {
    this.ws?.send(JSON.stringify({ subscribe: channels }))
  }

  private notifyStatus(connected: boolean) {
    this.statusHandlers.forEach(h => h(connected))
  }

  onStatus(handler: StatusHandler) {
    this.statusHandlers.add(handler)
    return () => this.statusHandlers.delete(handler)
  }

  on(channel: WsChannel, handler: Handler) {
    if (!this.handlers.has(channel)) this.handlers.set(channel, new Set())
    this.handlers.get(channel)!.add(handler)
    return () => this.handlers.get(channel)?.delete(handler)
  }

  disconnect() {
    this.intentionalClose = true
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.ws?.close()
  }
}

export const wsClient = new AnchorWebSocket()
