import { useQuery } from '@tanstack/react-query'
import { api } from '../client'
import type {
  SystemHealth,
  PerformanceSummary, EquityPoint, MonteCarloResult, Trade, EconomicEvent
} from '../../types'

export const useSystemHealth = () =>
  useQuery({ queryKey: ['system-health'], queryFn: () => api.get<SystemHealth>('/system/health'), refetchInterval: 60_000 })

export const usePerformance = () =>
  useQuery({ queryKey: ['performance'], queryFn: () => api.get<PerformanceSummary>('/performance/summary'), refetchInterval: 60_000 })

export const useEquityCurve = () =>
  useQuery({ queryKey: ['equity-curve'], queryFn: () => api.get<EquityPoint[]>('/performance/equity-curve'), refetchInterval: 60_000 })

export const useMonteCarlo = () =>
  useQuery({ queryKey: ['monte-carlo'], queryFn: () => api.get<MonteCarloResult>('/performance/monte-carlo'), staleTime: 5 * 60_000 })

export const useTradeJournal = () =>
  useQuery({ queryKey: ['trade-journal'], queryFn: () => api.get<Trade[]>('/performance/trade-journal'), staleTime: 60_000 })

export const useRegime = () =>
  useQuery({
    queryKey: ['regime'],
    queryFn: async () => {
      const rows = await api.get<{ instrument: string; regime: string; confidence: number }[]>('/regime/current')
      // Transform array → Record<instrument, {state, confidence}> to match WS/store shape
      return Object.fromEntries(rows.map(r => [r.instrument, { state: r.regime, confidence: r.confidence }])) as Record<string, { state: string; confidence: number }>
    },
    refetchInterval: 30_000,
  })

export const useCalendar = () =>
  useQuery({ queryKey: ['calendar'], queryFn: () => api.get<EconomicEvent[]>('/calendar/upcoming?hours_ahead=48&impact=HIGH,MEDIUM'), refetchInterval: 5 * 60_000 })

export const useLatestSignals = () =>
  useQuery({ queryKey: ['signals-latest'], queryFn: () => api.get<import('../../types').Signal[]>('/signals/latest?limit=50'), refetchInterval: 60_000 })

export const usePositions = () =>
  useQuery({ queryKey: ['positions'], queryFn: () => api.get<import('../../types').Position[]>('/positions'), refetchInterval: 10_000 })

export const usePendingOrders = () =>
  useQuery({ queryKey: ['pending-orders'], queryFn: () => api.get<import('../../types').Order[]>('/orders'), refetchInterval: 10_000 })

export const useCandles = (instrument: string, timeframe: string) => {
  // Refresh rate based on candle duration — no need to poll faster than the candle closes
  const interval = timeframe === '15m' ? 30_000 : timeframe === '1h' ? 60_000 : 5 * 60_000

  return useQuery({
    queryKey: ['candles', instrument, timeframe],
    queryFn: () => api.get<{ time: number; open: number; high: number; low: number; close: number }[]>(
      `/market-data/${instrument}/${timeframe}?limit=500`
    ),
    refetchInterval: interval,
    staleTime: interval - 5_000,
  })
}

export const useEdgeConfidence = () =>
  useQuery({
    queryKey: ['edge-confidence'],
    queryFn:  () => api.get<import('../../types').EdgeConfidence>('/system/edge-confidence'),
    refetchInterval: 5 * 60_000,
    staleTime: 4 * 60_000,
  })

export const useLatestPerfCheck = () =>
  useQuery({
    queryKey: ['perf-check'],
    queryFn: async () => {
      type RawEvent = {
        event_at: string; severity: string; message: string
        event_type: string; metadata: { summaries?: import('../../types').PerfCheckSummary[]; alerts?: string[] }
      }
      const events = await api.get<RawEvent[]>('/system/events?limit=20')
      const latest = events.find(e => e.event_type === 'LIVE_PERF_CHECK')
      if (!latest) return null
      return {
        event_at:  latest.event_at,
        severity:  latest.severity as 'INFO' | 'WARNING',
        message:   latest.message,
        summaries: latest.metadata?.summaries ?? [],
        alerts:    latest.metadata?.alerts ?? [],
      } satisfies import('../../types').PerfCheckResult
    },
    refetchInterval: 5 * 60_000,
    staleTime: 4 * 60_000,
  })

export const useMarketContext = () =>
  useQuery({
    queryKey: ['market-context'],
    queryFn: () => api.get<{
      dxy:  { value: number; change_5d_pct: number; trend: 'UP' | 'DOWN' | 'NEUTRAL' } | null
      vix:  { vix: number } | null
      atr_profile: Record<string, { current_atr_pips: number; avg_atr_pips: number; ratio: number; status: 'QUIET' | 'NORMAL' | 'VOLATILE' }>
      correlation: Record<string, Record<string, number>>
    }>('/market/context'),
    refetchInterval: 5 * 60_000,
    staleTime: 4 * 60_000,
  })

export type IntelligenceReport = {
  id: string
  type: 'PRESESSION' | 'POSTSESSION' | 'WEEKLY'
  created_at: string
  content: string
  tokens_used: number
  delivered: boolean
}

export type SessionQuality = {
  environment: 'TRENDING' | 'CHOPPY' | 'MIXED'
  confidence: number
  size_scale: number
  threshold_adjustment: number
  pair_rankings: string[]
}

export const useIntelligenceBrief = (reportType?: 'PRESESSION' | 'POSTSESSION' | 'WEEKLY') =>
  useQuery({
    queryKey: ['intelligence-brief', reportType ?? 'any'],
    queryFn: async () => {
      const qs = reportType ? `?report_type=${reportType}` : ''
      const res = await api.get<{ report: IntelligenceReport | null }>(`/intelligence/latest${qs}`)
      return res.report
    },
    refetchInterval: 5 * 60_000,
    staleTime: 4 * 60_000,
  })

export const useSessionQuality = () =>
  useQuery({
    queryKey: ['session-quality'],
    queryFn: () => api.get<SessionQuality>('/intelligence/session-quality'),
    refetchInterval: 5 * 60_000,
    staleTime: 4 * 60_000,
  })

export type TradeExplanation = {
  id: string
  created_at: string
  content: string
  trade_id: string | null
  instrument: string | null
  outcome: 'WIN' | 'LOSS' | null
  net_pl: number | null
  session: string | null
}

export const useTradeExplanations = (limit = 20) =>
  useQuery({
    queryKey: ['trade-explanations', limit],
    queryFn: async () => {
      const res = await api.get<{ explanations: TradeExplanation[] }>(
        `/intelligence/trade-explanations?limit=${limit}`
      )
      return res.explanations
    },
    refetchInterval: 5 * 60_000,
    staleTime: 4 * 60_000,
  })

export type JournalAnalysis = {
  id: string
  created_at: string
  content: string
  trade_count: number | null
  tokens_used: number | null
}

export const useJournalAnalysis = () =>
  useQuery({
    queryKey: ['journal-analysis'],
    queryFn: async () => {
      const res = await api.get<{ analysis: JournalAnalysis | null }>('/intelligence/journal-analysis')
      return res.analysis
    },
    refetchInterval: 30 * 60_000,
    staleTime: 29 * 60_000,
  })