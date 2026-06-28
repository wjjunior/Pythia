// Pure derived state for the opportunity radar. Business logic here, never in components.
import {
  CALIBRATION_MAX_DAYS_TO_RESOLVE,
  CALIBRATION_MIN_WIN_MULTIPLE,
  CALIBRATION_MIN_WIN_PROBABILITY,
} from "./ranking"
import type { ReviewStatus } from "./types"

export interface RadarFilters {
  maxDaysToResolve: number
  minWinMultiple: number
  minWinProbability: number
}

export const DEFAULT_RADAR_FILTERS: RadarFilters = {
  maxDaysToResolve: CALIBRATION_MAX_DAYS_TO_RESOLVE,
  minWinMultiple: CALIBRATION_MIN_WIN_MULTIPLE,
  minWinProbability: CALIBRATION_MIN_WIN_PROBABILITY,
}

export type ReviewBadgeVariant = "positive" | "negative" | "muted"

// A badge for an opportunity already acted on, so the queue isn't re-reviewed; null when untouched.
export const reviewStatusBadge = (
  status: ReviewStatus,
): { label: string; variant: ReviewBadgeVariant } | null => {
  switch (status) {
    case "open":
      return { label: "position open", variant: "positive" }
    case "resolved":
      return { label: "resolved", variant: "muted" }
    case "rejected":
      return { label: "rejected", variant: "negative" }
    default:
      return null
  }
}
