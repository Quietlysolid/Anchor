import { useEffect } from 'react'
// npm install react-router-dom @types/react-router-dom
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Sidebar } from './components/layout/Sidebar'
import Dashboard   from './pages/Dashboard'
import Performance from './pages/Performance'
import Journal     from './pages/Journal'
import Backtest    from './pages/Backtest'
import Settings    from './pages/Settings'
import { wsClient } from './api/websocket'
import { useLatestSignals } from './api/hooks'
import { useMarketStore, useSystemStore, useSignalStore, usePositionStore } from './store'
import type { LivePrice, Signal, Position } from './types'

function WsBootstrap() {
  const setPrice     = useMarketStore(s => s.setPrice)
  const { setWsConnected, setHeartbeat, setRegime } = useSystemStore()
  const pushSignal   = useSignalStore(s => s.pushSignal)
  const setPositions = usePositionStore(s => s.setPositions)
  const { data: seedSignals } = useLatestSignals()

  // Seed signal store from REST on page load (before first WS event arrives)
  useEffect(() => {
    if (seedSignals && seedSignals.length > 0) {
      const store = useSignalStore.getState()
      if (store.signals.length === 0) {
        seedSignals.forEach(s => pushSignal(s))
      }
    }
  }, [seedSignals])

  useEffect(() => {
    wsClient.connect()

    const unsubs = [
      wsClient.onStatus((connected) => setWsConnected(connected)),
      wsClient.on('ticks',     (d) => setPrice(d as LivePrice)),
      wsClient.on('signals',   (d) => pushSignal(d as Signal)),
      wsClient.on('positions', (d) => setPositions(d as Position[])),
      wsClient.on('regime',    (d) => setRegime(d as Record<string, { state: string; confidence: number }>)),
      wsClient.on('heartbeat', ()  => setHeartbeat()),
    ]

    return () => {
      unsubs.forEach(fn => fn())
      wsClient.disconnect()
      setWsConnected(false)
    }
  }, [])

  return null
}

export default function App() {
  return (
    <BrowserRouter>
      <WsBootstrap />
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-y-auto">
          <Routes>
            <Route path="/"            element={<Dashboard />} />
            <Route path="/performance" element={<Performance />} />
            <Route path="/journal"     element={<Journal />} />
            <Route path="/backtest"    element={<Backtest />} />
            <Route path="/settings"    element={<Settings />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}