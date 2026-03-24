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
  balance: number
  equity: number
  accountUpdatedAt: Date | null
  setWsConnected: (v: boolean) => void
  setHeartbeat: () => void
  setRegime: (r: Record<string, { state: string; confidence: number }>) => void
  setAccount: (balance: number, equity: number) => void
}

export const useSystemStore = create<SystemStore>(set => ({
  wsConnected: false,
  lastHeartbeat: null,
  currentRegime: {},
  balance: 0,
  equity: 0,
  accountUpdatedAt: null,
  setWsConnected: (v) => set({ wsConnected: v }),
  setHeartbeat: () => set({ lastHeartbeat: new Date() }),
  setRegime: (r) => set({ currentRegime: r }),
  setAccount: (balance, equity) => set({ balance, equity, accountUpdatedAt: new Date() }),
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

// ── Signal weights + thresholds (fetched once from backend) ───
interface RiskConfig {
  max_risk_per_trade:    number
  drawdown_reduce_pct:   number
  drawdown_halt_pct:     number
  monthly_halt_pct:      number
  daily_loss_limit_pct:  number
  spread_spike_multiplier: number
}

interface TargetsConfig {
  win_rate_good:      number
  win_rate_warn:      number
  profit_factor_good: number
  profit_factor_warn: number
  drawdown_good:      number
  drawdown_halt:      number
  sharpe_good:        number
  sharpe_warn:        number
}

interface WeightsStore {
  londonWeights: Record<string, number>
  lcrWeights:    Record<string, number>
  thresholds: { london: number; lcr: number }
  instruments: string[]
  risk: RiskConfig
  targets: TargetsConfig
  fetchWeights: () => Promise<void>
}

const DEFAULT_RISK: RiskConfig = {
  max_risk_per_trade:    0.01,
  drawdown_reduce_pct:   0.08,
  drawdown_halt_pct:     0.15,
  monthly_halt_pct:      0.06,
  daily_loss_limit_pct:  0.03,
  spread_spike_multiplier: 3.0,
}

const DEFAULT_TARGETS: TargetsConfig = {
  win_rate_good:      0.47,
  win_rate_warn:      0.40,
  profit_factor_good: 1.30,
  profit_factor_warn: 1.00,
  drawdown_good:      0.08,
  drawdown_halt:      0.15,
  sharpe_good:        1.0,
  sharpe_warn:        0.5,
}

export const useWeightsStore = create<WeightsStore>(set => ({
  londonWeights: {},
  lcrWeights:    {},
  thresholds: { london: 0.72, lcr: 0.55 },
  instruments: ['EUR_USD', 'GBP_USD', 'NZD_USD', 'USD_CAD', 'EUR_JPY', 'AUD_USD'],
  risk:    DEFAULT_RISK,
  targets: DEFAULT_TARGETS,
  fetchWeights: async () => {
    try {
      const res = await fetch('/api/v1/signals/weights')
      const data = await res.json()
      set({
        londonWeights: data.london ?? {},
        lcrWeights:    data.lcr    ?? {},
        thresholds: {
          london: data.thresholds?.london ?? 0.72,
          lcr:    data.thresholds?.lcr    ?? 0.55,
        },
        instruments: data.instruments ?? ['EUR_USD', 'GBP_USD', 'NZD_USD', 'USD_CAD', 'EUR_JPY', 'AUD_USD'],
        risk:    { ...DEFAULT_RISK,    ...(data.risk    ?? {}) },
        targets: { ...DEFAULT_TARGETS, ...(data.targets ?? {}) },
      })
    } catch {
      // keep fallback defaults
    }
  },
}))

// ── Live positions (WebSocket override) ───────────────────────
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
