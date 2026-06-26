// Pure derived state for the views. Business logic lives here, never in components (§ coding rule 8).
import type { GateDecision } from "./types"

export const EXPOSURE_WARN = 0.6
export const EXPOSURE_HIGH = 0.9

// §8.5 calibration gate (PYT-101): a category is "cleared" only when Brier(system) beats the market
// over min-n with EV honest — not just when it has enough data. Absent ⇒ no resolved markets yet.
export type GateState = "cleared" | "gated"
export const gateState = (gate: GateDecision | undefined): GateState =>
  gate?.cleared ? "cleared" : "gated"

export const GATE_REASON_LABELS: Record<string, string> = {
  insufficient_n: "not enough resolved n",
  no_brier_edge: "no Brier edge vs market",
  ev_not_honest: "EV not yet honest",
  poor_resolution: "resolution quality too low",
}

export const gateBlockedReasons = (gate: GateDecision | undefined): string =>
  gate && gate.reasons.length > 0
    ? gate.reasons.map((reason) => GATE_REASON_LABELS[reason] ?? reason).join(", ")
    : "no resolved markets yet"

// Calibration queue: surface markets that RESOLVE soon (not just close trading) and are still
// enterable, so calibration feedback arrives fast (PYT-75/76/82).
export const CALIBRATION_MAX_DAYS_TO_RESOLVE = 7
export const CALIBRATION_MIN_WIN_MULTIPLE = 1.2
// The recommended (edge) side must also be the side the model expects to win — no penny longshots.
export const CALIBRATION_MIN_WIN_PROBABILITY = 0.5

// Cohort calibration display (PYT-98). `overall` is the headline (all resolved markets); the slices
// are labeled subsets that OVERLAP and never sum to overall.
export const OVERALL_COHORT = "overall"
export const COHORT_SLICES = ["fast_7d", "midband"] as const
export const COHORT_LABELS: Record<string, string> = {
  overall: "Overall",
  fast_7d: "Fast (≤7d)",
  midband: "Mid-band price",
}
// Below this n a cohort's Brier is too noisy to read as a calibration claim — flag, don't mislead.
export const COHORT_MIN_N = 30

export type CalibrationVerdict = "system" | "market" | "tie"
export type ExposureSeverity = "ok" | "warn" | "high"
export type CohortConfidence = "ok" | "low"

// delta = brier_market − brier_system: positive ⇒ the system is better calibrated than the market.
export const calibrationVerdict = (item: { delta: number }): CalibrationVerdict => {
  if (item.delta > 0) return "system"
  if (item.delta < 0) return "market"
  return "tie"
}

export const cohortConfidence = (n: number): CohortConfidence => (n < COHORT_MIN_N ? "low" : "ok")

export const exposureSeverity = (fraction: number): ExposureSeverity => {
  if (fraction >= EXPOSURE_HIGH) return "high"
  if (fraction >= EXPOSURE_WARN) return "warn"
  return "ok"
}
