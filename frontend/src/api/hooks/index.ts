import { useQuery } from '@tanstack/react-query'
import { api } from '../client'
import type { HomepageSnapshot, Position } from '../../types'

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
