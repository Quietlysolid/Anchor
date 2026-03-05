import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import { wsClient } from '../websocket'
import type {
  SystemHealth,
  PerformanceSummary, EquityPoint, MonteCarloResult, Trade, EconomicEvent, LivePrice
} from '../../types'

export const useSystemHealth = () =>
  useQuery({ queryKey: ['system-health'], queryFn: () => api.get<SystemHealth>('/system/health'), refetchInterval: 15_000 })

export const usePerformance = () =>
  useQuery({ queryKey: ['performance'], queryFn: () => api.get<PerformanceSummary>('/performance/summary'), refetchInterval: 60_000 })

export const useEquityCurve = () =>
  useQuery({ queryKey: ['equity-curve'], queryFn: () => api.get<EquityPoint[]>('/performance/equity-curve'), refetchInterval: 60_000 })

export const useMonteCarlo = () =>
  useQuery({ queryKey: ['monte-carlo'], queryFn: () => api.get<MonteCarloResult>('/performance/monte-carlo'), staleTime: 5 * 60_000 })

export const useTradeJournal = () =>
  useQuery({ queryKey: ['trade-journal'], queryFn: () => api.get<Trade[]>('/performance/trade-journal'), staleTime: 60_000 })

export const useRegime = () =>
  useQuery({ queryKey: ['regime'], queryFn: () => api.get<Record<string, { state: string; confidence: number }>>('/regime/current'), refetchInterval: 30_000 })

export const useCalendar = () =>
  useQuery({ queryKey: ['calendar'], queryFn: () => api.get<EconomicEvent[]>('/calendar/upcoming?hours_ahead=48&impact=HIGH,MEDIUM'), refetchInterval: 5 * 60_000 })

export const useCandles = (instrument: string, timeframe: string) => {
  const qc = useQueryClient()
  const key = ['candles', instrument, timeframe]

  // Invalidate the candle query on each tick for this instrument so the
  // chart stays current without waiting for the 5-second polling fallback.
  useEffect(() => {
    const unsub = wsClient.on('ticks', (d) => {
      const tick = d as LivePrice
      if (tick.instrument === instrument) {
        qc.invalidateQueries({ queryKey: key })
      }
    })
    return () => { unsub() }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrument])

  return useQuery({
    queryKey: key,
    queryFn: () => api.get<{ time: number; open: number; high: number; low: number; close: number }[]>(
      `/market-data/${instrument}/${timeframe}?limit=300`
    ),
    refetchInterval: 5_000, // fallback poll when WS is down
  })
}