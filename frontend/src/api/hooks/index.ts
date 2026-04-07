import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type { HomepageSnapshot, ManualTradeJournalEntry, ManualTradingProfile, Position } from '../../types'

export const useHomepageSnapshot = () =>
  useQuery({
    queryKey: ['homepage-snapshot'],
    queryFn: () => api.get<HomepageSnapshot>('/system/homepage-snapshot'),
    refetchInterval: 15_000,
    staleTime: 10_000,
  })

export const usePositions = () =>
  useQuery({
    queryKey: ['positions'],
    queryFn: () => api.get<Position[]>('/positions'),
    refetchInterval: 10_000,
  })

export const useManualTradeJournal = () =>
  useQuery({
    queryKey: ['manual-trades'],
    queryFn: () => api.get<ManualTradeJournalEntry[]>('/manual-trades'),
    staleTime: 10_000,
  })

export const useManualTradingProfile = () =>
  useQuery({
    queryKey: ['manual-trades-profile'],
    queryFn: () => api.get<ManualTradingProfile | null>('/manual-trades/profile'),
    staleTime: 10_000,
  })

export const useUpsertManualTradeJournal = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: Omit<ManualTradeJournalEntry, 'id' | 'created_at' | 'updated_at'>) =>
      api.post<ManualTradeJournalEntry>('/manual-trades', body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['manual-trades'] })
    },
  })
}

export const useDeleteManualTradeJournal = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (actionKey: string) =>
      api.delete<{ ok: boolean }>(`/manual-trades/${encodeURIComponent(actionKey)}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['manual-trades'] })
    },
  })
}

export const useUpsertManualTradingProfile = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: { starting_balance: number | null }) =>
      api.post<ManualTradingProfile>('/manual-trades/profile', body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['manual-trades-profile'] })
    },
  })
}
