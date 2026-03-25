import { useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { DisconnectedBanner } from './components/layout/DisconnectedBanner'
import Home from './pages/Home'
import { ErrorBoundary } from './components/ui/ErrorBoundary'
import { wsClient } from './api/websocket'
import { useLatestSignals, usePositions, useRegime } from './api/hooks'
import { useMarketStore, usePositionStore, useSignalStore, useSystemStore, useWeightsStore } from './store'
import type { LivePrice, Position, Signal } from './types'

function WsBootstrap() {
  const setPrice = useMarketStore((s) => s.setPrice)
  const { setWsConnected, setHeartbeat, setRegime, setAccount } = useSystemStore()
  const pushSignal = useSignalStore((s) => s.pushSignal)
  const setPositions = usePositionStore((s) => s.setPositions)
  const fetchWeights = useWeightsStore((s) => s.fetchWeights)
  const queryClient = useQueryClient()
  const { data: seedSignals } = useLatestSignals()
  const { data: seedRegime } = useRegime()
  const { data: seedPositions } = usePositions()

  useEffect(() => {
    fetchWeights()
  }, [fetchWeights])

  useEffect(() => {
    if (seedSignals?.length && useSignalStore.getState().signals.length === 0) {
      seedSignals.forEach((signal) => pushSignal(signal))
    }
  }, [pushSignal, seedSignals])

  useEffect(() => {
    if (seedRegime && Object.keys(seedRegime).length > 0) {
      if (Object.keys(useSystemStore.getState().currentRegime).length === 0) {
        setRegime(seedRegime)
      }
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
      wsClient.on('ticks', (d) => setPrice(d as LivePrice)),
      wsClient.on('signals', (d) => pushSignal(d as Signal)),
      wsClient.on('regime', (d) => setRegime(d as Record<string, { state: string; confidence: number }>)),
      wsClient.on('heartbeat', () => setHeartbeat()),
      wsClient.on('account', (d) => {
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
      unsubs.forEach((fn) => fn())
      wsClient.disconnect()
      setWsConnected(false)
    }
  }, [pushSignal, queryClient, setAccount, setHeartbeat, setPositions, setPrice, setRegime, setWsConnected])

  return null
}

function Layout() {
  const { wsConnected } = useSystemStore()

  return (
    <div className="min-h-screen text-anchor-navy">
      <DisconnectedBanner visible={!wsConnected} />
      <main className="min-h-screen">
        <Routes>
          <Route path="/" element={<ErrorBoundary label="Home"><Home /></ErrorBoundary>} />
          <Route path="*" element={<Navigate to="/" replace />} />
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
