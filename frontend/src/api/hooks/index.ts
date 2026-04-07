import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../client'
import type { HomepageSnapshot, ManualTradeJournalEntry, Position } from '../../types'

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
