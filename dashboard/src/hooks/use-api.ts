import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { qk } from "@/lib/query-keys"
import {
  CALIBRATION_MAX_DAYS_TO_RESOLVE,
  CALIBRATION_MIN_WIN_MULTIPLE,
  CALIBRATION_MIN_WIN_PROBABILITY,
} from "@/lib/ranking"
import type { OverrideAction, ReviewRequest } from "@/lib/types"

export const useRadar = () =>
  useQuery({
    queryKey: qk.radar,
    queryFn: () =>
      api.radar({
        maxDaysToResolve: CALIBRATION_MAX_DAYS_TO_RESOLVE,
        minWinMultiple: CALIBRATION_MIN_WIN_MULTIPLE,
        minWinProbability: CALIBRATION_MIN_WIN_PROBABILITY,
      }),
  })

export const useMarket = (id: string) =>
  useQuery({ queryKey: qk.market(id), queryFn: () => api.market(id), enabled: Boolean(id) })

export const usePortfolio = () => useQuery({ queryKey: qk.portfolio, queryFn: api.portfolio })

export const usePaper = () => useQuery({ queryKey: qk.paper, queryFn: api.paper })

export const useCalibration = (category?: string) =>
  useQuery({ queryKey: qk.calibration(category), queryFn: () => api.calibration(category) })

export const useCost = () => useQuery({ queryKey: qk.cost, queryFn: api.cost })

export const useArbs = () => useQuery({ queryKey: qk.arbs, queryFn: api.arbs })

export const useApproveArb = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, baskets }: { id: string; baskets: number }) => api.approveArb(id, baskets),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: qk.arbs })
      void client.invalidateQueries({ queryKey: qk.paper })
      void client.invalidateQueries({ queryKey: qk.portfolio })
    },
  })
}

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

export const useRoster = () => useQuery({ queryKey: qk.roster, queryFn: api.roster })

export const useSmartCandidates = () =>
  useQuery({ queryKey: qk.smartCandidates, queryFn: () => api.smartCandidates() })

export const useRosterHistory = () =>
  useQuery({ queryKey: qk.rosterEvents, queryFn: () => api.rosterEvents() })

export const useOverride = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ traderId, action }: { traderId: string; action: OverrideAction }) =>
      api.override(traderId, action),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: qk.roster })
      void client.invalidateQueries({ queryKey: qk.rosterEvents })
    },
  })
}
