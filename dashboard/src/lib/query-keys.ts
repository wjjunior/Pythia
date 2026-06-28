import type { RadarFilters } from "./radar"

export const qk = {
  radar: (filters?: RadarFilters) => ["radar", filters ?? null] as const,
  markets: (category?: string) => ["markets", category ?? null] as const,
  market: (id: string) => ["market", id] as const,
  estimate: (id: string) => ["estimate", id] as const,
  portfolio: ["portfolio"] as const,
  paper: ["paper"] as const,
  calibration: (category?: string) => ["calibration", category ?? null] as const,
  cost: ["cost"] as const,
  simulation: (category?: string) => ["simulation", category ?? null] as const,
  roster: ["roster"] as const,
  smartCandidates: ["smart-candidates"] as const,
  rosterEvents: ["roster-events"] as const,
  arbs: ["arbs"] as const,
}
