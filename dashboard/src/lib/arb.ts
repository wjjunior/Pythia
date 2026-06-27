// Pure derived state for the arbitrage panel. Business logic lives here, never in components.
import type { ArbItem } from "./types"

export const ARB_KIND_LABELS: Record<string, string> = {
  dutch_book: "Dutch book",
  ladder_spread: "Ladder spread",
}

export const arbKindLabel = (kind: string): string => ARB_KIND_LABELS[kind] ?? kind

export const basketCost = (arb: ArbItem): number =>
  arb.legs.reduce((total, leg) => total + leg.price, 0)

// The locked profit as a fraction of the capital one basket deploys — the panel's ranking-tiebreak
// readout. Zero when the basket has no cost (degenerate; avoids divide-by-zero).
export const returnOnCost = (arb: ArbItem): number => {
  const cost = basketCost(arb)
  return cost > 0 ? arb.ev / cost : 0
}

export const rankByEv = (arbs: ArbItem[]): ArbItem[] => [...arbs].sort((a, b) => b.ev - a.ev)
