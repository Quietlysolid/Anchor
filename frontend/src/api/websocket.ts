import type { WsMessage, WsChannel } from '../types'

type Handler = (data: unknown) => void

class AnchorWebSocket {
  private ws: WebSocket | null = null
  private handlers: Map<WsChannel, Set<Handler>> = new Map()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private intentionalClose = false

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    this.ws = new WebSocket(`${proto}://${location.host}/ws`)

    this.ws.onopen = () => {
      console.info('[WS] connected')
      this.subscribe(['ticks', 'signals', 'positions', 'orders', 'regime', 'heartbeat'])
    }

    this.ws.onmessage = (evt) => {
      try {
        const msg: WsMessage = JSON.parse(evt.data)
        this.handlers.get(msg.channel)?.forEach(h => h(msg.data))
      } catch { /* ignore malformed */ }
    }

    this.ws.onclose = () => {
      if (!this.intentionalClose) {
        console.warn('[WS] disconnected — reconnecting in 3s')
        this.reconnectTimer = setTimeout(() => this.connect(), 3000)
      }
    }
  }

  private subscribe(channels: WsChannel[]) {
    this.ws?.send(JSON.stringify({ subscribe: channels }))
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