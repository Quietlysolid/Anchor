import { useEffect, useState } from 'react'
// npm install react-router-dom @types/react-router-dom
import { BrowserRouter, Routes, Route, useLocation } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Sidebar } from './components/layout/Sidebar'
import Dashboard   from './pages/Dashboard'
import Performance from './pages/Performance'
import Journal     from './pages/Journal'
import Backtest    from './pages/Backtest'
import Settings    from './pages/Settings'
import { wsClient } from './api/websocket'
import { useLatestSignals, useRegime, usePositions } from './api/hooks'
import { useMarketStore, useSystemStore, useSignalStore, usePositionStore, useWeightsStore } from './store'
import type { LivePrice, Signal, Position } from './types'

function WsBootstrap() {
  const setPrice      = useMarketStore(s => s.setPrice)
  const { setWsConnected, setHeartbeat, setRegime, setAccount } = useSystemStore()
  const pushSignal    = useSignalStore(s => s.pushSignal)
  const setPositions  = usePositionStore(s => s.setPositions)
  const fetchWeights  = useWeightsStore(s => s.fetchWeights)
  const queryClient   = useQueryClient()
  const { data: seedSignals  } = useLatestSignals()
  const { data: seedRegime   } = useRegime()
  const { data: seedPositions } = usePositions()

  // Fetch signal weights once on load
  useEffect(() => { fetchWeights() }, [])

  // Seed signal store from REST on page load (before first WS event arrives)
  useEffect(() => {
    if (seedSignals && seedSignals.length > 0) {
      const store = useSignalStore.getState()
      if (store.signals.length === 0) seedSignals.forEach(s => pushSignal(s))
    }
  }, [seedSignals])

  // Seed regime store from REST on page load
  useEffect(() => {
    if (seedRegime && Object.keys(seedRegime).length > 0) {
      if (Object.keys(useSystemStore.getState().currentRegime).length === 0) setRegime(seedRegime)
    }
  }, [seedRegime])

  // Seed position store from REST on page load (WS takes over after first broadcast)
  useEffect(() => {
    if (seedPositions && usePositionStore.getState().positions.length === 0) {
      setPositions(seedPositions)
    }
  }, [seedPositions])

  useEffect(() => {
    wsClient.connect()

    const unsubs = [
      wsClient.onStatus((connected) => setWsConnected(connected)),
      wsClient.on('ticks',     (d) => setPrice(d as LivePrice)),
      wsClient.on('signals',   (d) => pushSignal(d as Signal)),
      wsClient.on('regime',    (d) => setRegime(d as Record<string, { state: string; confidence: number }>)),
      wsClient.on('heartbeat', ()  => setHeartbeat()),
      wsClient.on('account',   (d) => {
        const { balance, equity } = d as { balance: number; equity: number }
        setAccount(balance, equity)
      }),
      wsClient.on('positions', (d) => {
        setPositions(d as Position[])
        // A position change means trades closed/opened — invalidate perf data
        queryClient.invalidateQueries({ queryKey: ['performance'] })
        queryClient.invalidateQueries({ queryKey: ['trade-journal'] })
        queryClient.invalidateQueries({ queryKey: ['equity-curve'] })
      }),
      wsClient.on('orders', () => {
        queryClient.invalidateQueries({ queryKey: ['pending-orders'] })
      }),
    ]

    return () => {
      unsubs.forEach(fn => fn())
      wsClient.disconnect()
      setWsConnected(false)
    }
  }, [])

  return null
}

function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const location = useLocation()

  // Close sidebar on navigation
  useEffect(() => { setSidebarOpen(false) }, [location.pathname])

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-20 bg-black/60 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar — drawer on mobile, fixed on desktop */}
      <div className={`fixed inset-y-0 left-0 z-30 transition-transform duration-200 md:relative md:translate-x-0 md:z-auto
        ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}`}>
        <Sidebar onClose={() => setSidebarOpen(false)} />
      </div>

      {/* Main */}
      <main className="flex-1 overflow-y-auto min-w-0">
        {/* Mobile top bar */}
        <div className="sticky top-0 z-10 flex items-center gap-3 px-4 py-3 bg-background border-b border-border md:hidden">
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            className="text-muted-foreground hover:text-foreground p-1"
            aria-label="Open menu"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <rect y="3" width="20" height="2" rx="1"/>
              <rect y="9" width="20" height="2" rx="1"/>
              <rect y="15" width="20" height="2" rx="1"/>
            </svg>
          </button>
          <span className="text-primary text-lg">⚓</span>
          <span className="font-bold text-sm tracking-tight">ANCHOR</span>
        </div>

        <Routes>
          <Route path="/"            element={<Dashboard />} />
          <Route path="/performance" element={<Performance />} />
          <Route path="/journal"     element={<Journal />} />
          <Route path="/backtest"    element={<Backtest />} />
          <Route path="/settings"    element={<Settings />} />
        </Routes>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <WsBootstrap />
      <Layout />
    </BrowserRouter>
  )
}