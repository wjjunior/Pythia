// Pure derived state for the views. Business logic lives here, never in components (§ coding rule 8).
import type { CategoryCalibration } from "./types"

export const EXPOSURE_WARN = 0.6
export const EXPOSURE_HIGH = 0.9

// Calibration queue: surface near-term, high-payout markets so resolutions arrive fast (PYT-75/76).
export const CALIBRATION_MAX_DAYS_TO_CLOSE = 7
export const CALIBRATION_MIN_WIN_MULTIPLE = 1.4
// The recommended (edge) side must also be the side the model expects to win — no penny longshots.
export const CALIBRATION_MIN_WIN_PROBABILITY = 0.5

export type CalibrationVerdict = "system" | "market" | "tie"
export type ExposureSeverity = "ok" | "warn" | "high"

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
