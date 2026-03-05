import { create } from 'zustand'
import type { LivePrice, Position, Signal } from '../types'

// ── Live price ticks (updated via WebSocket) ──────────────────
interface MarketStore {
  prices: Record<string, LivePrice>
  setPrice: (p: LivePrice) => void
}

export const useMarketStore = create<MarketStore>(set => ({
  prices: {},
  setPrice: (p) => set(s => ({ prices: { ...s.prices, [p.instrument]: p } })),
}))

// ── System / connection state ──────────────────────────────────
interface SystemStore {
  wsConnected: boolean
  lastHeartbeat: Date | null
  currentRegime: Record<string, { state: string; confidence: number }>
  setWsConnected: (v: boolean) => void
  setHeartbeat: () => void
  setRegime: (r: Record<string, { state: string; confidence: number }>) => void
}

export const useSystemStore = create<SystemStore>(set => ({
  wsConnected: false,
  lastHeartbeat: null,
  currentRegime: {},
  setWsConnected: (v) => set({ wsConnected: v }),
  setHeartbeat: () => set({ lastHeartbeat: new Date() }),
  setRegime: (r) => set({ currentRegime: r }),
}))

// ── Live signals feed (last 50) ───────────────────────────────
interface SignalStore {
  signals: Signal[]
  pushSignal: (s: Signal) => void
}

export const useSignalStore = create<SignalStore>(set => ({
  signals: [],
  pushSignal: (s) => set(st => ({ signals: [s, ...st.signals].slice(0, 50) })),
}))

// ── Live positions (WebSocket override) ───────────────────────
interface PositionStore {
  positions: Position[]
  setPositions: (p: Position[]) => void
}

export const usePositionStore = create<PositionStore>(set => ({
  positions: [],
  setPositions: (positions) => set({ positions }),
}))