// Pure derived state for the views. Business logic lives here, never in components (§ coding rule 8).
import type { CategoryCalibration, RadarItem, Score, Side } from "./types"

export const EXPOSURE_WARN = 0.6
export const EXPOSURE_HIGH = 0.9

export type CalibrationVerdict = "system" | "market" | "tie"
export type ExposureSeverity = "ok" | "warn" | "high"

// Radar AC: ranked by opportunity_score, highest first. Does not mutate the input.
export const rankByOpportunity = (items: RadarItem[]): RadarItem[] =>
  [...items].sort((a, b) => b.score.opportunity_score - a.score.opportunity_score)

// edge = p_model − q (signed): a positive edge backs YES, a negative edge backs NO.
export const impliedSide = (score: Score): Side => (score.edge >= 0 ? "YES" : "NO")

// q is not carried on the radar item, but q = p_model − edge.
export const marketPrice = (item: RadarItem): number => item.estimate.p_model - item.score.edge

// delta = brier_market − brier_system: positive ⇒ the system is better calibrated than the market.
export const calibrationVerdict = (category: CategoryCalibration): CalibrationVerdict => {
  if (category.delta > 0) return "system"
  if (category.delta < 0) return "market"
  return "tie"
}

export const exposureSeverity = (fraction: number): ExposureSeverity => {
  if (fraction >= EXPOSURE_HIGH) return "high"
  if (fraction >= EXPOSURE_WARN) return "warn"
  return "ok"
}
