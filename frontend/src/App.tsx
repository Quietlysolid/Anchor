import { useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { DisconnectedBanner } from './components/layout/DisconnectedBanner'
import Home from './pages/Home'
import { ErrorBoundary } from './components/ui/ErrorBoundary'
import { wsClient } from './api/websocket'
import { usePositions } from './api/hooks'
import { usePositionStore, useSystemStore } from './store'
import type { Position } from './types'

function WsBootstrap() {
  const { setWsConnected, setHeartbeat, setAccount } = useSystemStore()
  const setPositions = usePositionStore((s) => s.setPositions)
  const queryClient = useQueryClient()
  const { data: seedPositions } = usePositions()

  useEffect(() => {
    if (seedPositions) {
      setPositions(seedPositions)
    }
  }, [seedPositions, setPositions])

  useEffect(() => {
    wsClient.connect()

    const unsubs = [
      wsClient.onStatus((connected) => setWsConnected(connected)),
      wsClient.on('heartbeat', () => setHeartbeat()),
      wsClient.on('account', (d) => {
        const { balance, equity } = d as { balance: number; equity: number }
        setAccount(balance, equity)
        queryClient.invalidateQueries({ queryKey: ['homepage-snapshot'] })
      }),
      wsClient.on('positions', (d) => {
        setPositions(d as Position[])
        queryClient.invalidateQueries({ queryKey: ['homepage-snapshot'] })
        queryClient.invalidateQueries({ queryKey: ['performance'] })
        queryClient.invalidateQueries({ queryKey: ['trade-journal'] })
        queryClient.invalidateQueries({ queryKey: ['equity-curve'] })
      }),
      wsClient.on('orders', () => {
        queryClient.invalidateQueries({ queryKey: ['homepage-snapshot'] })
        queryClient.invalidateQueries({ queryKey: ['pending-orders'] })
      }),
      wsClient.on('events', () => {
        queryClient.invalidateQueries({ queryKey: ['homepage-snapshot'] })
      }),
    ]

    return () => {
      unsubs.forEach((fn) => fn())
      wsClient.disconnect()
      setWsConnected(false)
    }
  }, [queryClient, setAccount, setHeartbeat, setPositions, setWsConnected])

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
