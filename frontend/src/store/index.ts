import { create } from 'zustand'
import type { Position } from '../types'

interface SystemStore {
  wsConnected: boolean
  lastHeartbeat: Date | null
  balance: number
  equity: number
  accountUpdatedAt: Date | null
  setWsConnected: (v: boolean) => void
  setHeartbeat: () => void
  setAccount: (balance: number, equity: number) => void
}

export const useSystemStore = create<SystemStore>(set => ({
  wsConnected: false,
  lastHeartbeat: null,
  balance: 0,
  equity: 0,
  accountUpdatedAt: null,
  setWsConnected: (v) => set({ wsConnected: v }),
  setHeartbeat: () => set({ lastHeartbeat: new Date() }),
  setAccount: (balance, equity) => set({ balance, equity, accountUpdatedAt: new Date() }),
}))

interface PositionStore {
  positions: Position[]
  positionsUpdatedAt: Date | null
  setPositions: (p: Position[]) => void
}

export const usePositionStore = create<PositionStore>(set => ({
  positions: [],
  positionsUpdatedAt: null,
  setPositions: (positions) => set({ positions, positionsUpdatedAt: new Date() }),
}))
