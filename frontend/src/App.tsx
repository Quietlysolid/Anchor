import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, useLocation, Navigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Sidebar } from './components/layout/Sidebar'
import { BottomNav } from './components/layout/BottomNav'
import { DisconnectedBanner } from './components/layout/DisconnectedBanner'
import Console      from './pages/Console'
import Intelligence from './pages/Intelligence'
import Trades       from './pages/Trades'
import { ErrorBoundary } from './components/ui/ErrorBoundary'
import { wsClient }  from './api/websocket'
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
  const { data: seedSignals   } = useLatestSignals()
  const { data: seedRegime    } = useRegime()
  const { data: seedPositions } = usePositions()

  useEffect(() => { fetchWeights() }, [fetchWeights])

  useEffect(() => {
    if (seedSignals?.length && useSignalStore.getState().signals.length === 0) {
      seedSignals.forEach(s => pushSignal(s))
    }
  }, [pushSignal, seedSignals])

  useEffect(() => {
    if (seedRegime && Object.keys(seedRegime).length > 0) {
      if (Object.keys(useSystemStore.getState().currentRegime).length === 0) setRegime(seedRegime)
    }
  }, [seedRegime, setRegime])

  useEffect(() => {
    if (seedPositions) {
      setPositions(seedPositions)
    }
  }, [seedPositions, setPositions])

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
  }, [pushSignal, queryClient, setAccount, setHeartbeat, setPositions, setPrice, setRegime, setWsConnected])

  return null
}

function Layout() {
  const [sidebarOpen, setSidebarOpen]     = useState(false)
  const [bannerDismissed, setBannerDismissed] = useState(false)
  const { wsConnected } = useSystemStore()
  const location = useLocation()

  useEffect(() => { setSidebarOpen(false) }, [location.pathname])
  useEffect(() => { if (wsConnected) setBannerDismissed(false) }, [wsConnected])

  const showBanner = !wsConnected && !bannerDismissed

  return (
    <div className="flex min-h-screen md:h-screen overflow-hidden bg-anchor-void">
      <DisconnectedBanner
        visible={showBanner}
        onDismiss={() => setBannerDismissed(true)}
      />

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
      <main className={`flex-1 overflow-y-auto min-w-0 transition-all ${showBanner ? 'pt-9' : ''}`}>
        {/* Mobile top bar — branding only, nav is in BottomNav */}
        <div className="sticky top-0 z-10 flex items-center gap-3 px-4 py-3 bg-anchor-void border-b border-anchor-border md:hidden">
          <svg width="16" height="16" viewBox="0 0 20 20" fill="none" className="text-anchor-green">
            <circle cx="10" cy="4"  r="2.2" stroke="currentColor" strokeWidth="1.5"/>
            <line x1="10" y1="6.2"  x2="10"  y2="17"  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            <line x1="5"  y1="8.8"  x2="15"  y2="8.8"  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            <path d="M10 17 Q 5.5 17.5 4.5 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none"/>
            <path d="M10 17 Q 14.5 17.5 15.5 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none"/>
          </svg>
          <span className="font-mono font-semibold text-sm tracking-[0.15em] text-anchor-text">ANCHOR</span>
        </div>

        <Routes>
          <Route path="/"             element={<ErrorBoundary label="Console"><Console /></ErrorBoundary>} />
          <Route path="/intelligence" element={<ErrorBoundary label="Intelligence"><Intelligence /></ErrorBoundary>} />
          <Route path="/trades"       element={<ErrorBoundary label="Trades"><Trades /></ErrorBoundary>} />
          {/* Legacy redirects */}
          <Route path="/performance"  element={<Navigate to="/trades" replace />} />
          <Route path="/journal"      element={<Navigate to="/intelligence" replace />} />
          <Route path="/settings"     element={<Navigate to="/" replace />} />
        </Routes>

        {/* Mobile bottom navigation */}
        <BottomNav />
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
