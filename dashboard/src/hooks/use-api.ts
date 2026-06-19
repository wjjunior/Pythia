import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { qk } from "@/lib/query-keys"
import type { ReviewRequest } from "@/lib/types"

export const useRadar = (limit = 50) =>
  useQuery({ queryKey: qk.radar, queryFn: () => api.radar(limit) })

export const useMarket = (id: string) =>
  useQuery({ queryKey: qk.market(id), queryFn: () => api.market(id), enabled: Boolean(id) })

export const usePortfolio = () => useQuery({ queryKey: qk.portfolio, queryFn: api.portfolio })

export const usePaper = () => useQuery({ queryKey: qk.paper, queryFn: api.paper })

export const useCalibration = (category?: string) =>
  useQuery({ queryKey: qk.calibration(category), queryFn: () => api.calibration(category) })

export const useCost = () => useQuery({ queryKey: qk.cost, queryFn: api.cost })

export const useSimulation = (category?: string) =>
  useQuery({ queryKey: qk.simulation(category), queryFn: () => api.simulation(category) })

export const useReview = (estimateId: string) => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: ReviewRequest) => api.review(estimateId, body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: qk.paper })
      void client.invalidateQueries({ queryKey: qk.portfolio })
      void client.invalidateQueries({ queryKey: qk.radar })
    },
  })
}
