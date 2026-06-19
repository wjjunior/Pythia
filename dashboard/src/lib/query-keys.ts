export const qk = {
  radar: ["radar"] as const,
  markets: (category?: string) => ["markets", category ?? null] as const,
  market: (id: string) => ["market", id] as const,
  estimate: (id: string) => ["estimate", id] as const,
  portfolio: ["portfolio"] as const,
  paper: ["paper"] as const,
  calibration: (category?: string) => ["calibration", category ?? null] as const,
  cost: ["cost"] as const,
  simulation: (category?: string) => ["simulation", category ?? null] as const,
}
