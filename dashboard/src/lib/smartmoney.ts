// Pure derived state for the smart-money panels — logic lives here, never in components (rule 8).
import type { RosterEntry, TraderState } from "./types"

const STATE_ORDER: Record<TraderState, number> = { active: 0, trial: 1, benched: 2, retired: 3 }

export const STATE_BADGE: Record<TraderState, "positive" | "muted" | "outline" | "negative"> = {
  active: "positive",
  trial: "muted",
  benched: "outline",
  retired: "negative",
}

export const shortWallet = (wallet: string): string =>
  wallet.length > 12 ? `${wallet.slice(0, 6)}…${wallet.slice(-4)}` : wallet

// Active first, then trial / benched / retired; within a state, strongest trader_score first.
export const rankRoster = (entries: RosterEntry[]): RosterEntry[] =>
  [...entries].sort((a, b) => {
    const byState = STATE_ORDER[a.trader.state] - STATE_ORDER[b.trader.state]
    if (byState !== 0) return byState
    return (
      (b.scorecard?.trader_score ?? Number.NEGATIVE_INFINITY) -
      (a.scorecard?.trader_score ?? Number.NEGATIVE_INFINITY)
    )
  })
