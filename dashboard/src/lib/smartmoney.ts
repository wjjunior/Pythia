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

// Definitions for the cryptic scorecard columns, surfaced as hover tooltips.
export const SCORECARD_HINTS = {
  score: "Composite promotion score — higher is stronger; it ranks the roster.",
  n: "Resolved entries behind the score — more entries means a more reliable score.",
  agree: "Share of this wallet's entries where our pipeline also flagged +EV.",
  bpd: "$-weighted Brier improvement vs the market on the agreed subset — the calibration signal.",
  sortino: "Downside-risk-adjusted return (mean excess ÷ downside deviation).",
  max_dd: "Worst peak-to-trough drawdown of the wallet's cumulative return.",
} as const

export const VERDICT_HINT =
  "viable = our model also found +EV on the triggered market; no edge = the model disagreed."

export const PIN_HINT = "Force this wallet to active — its entries start triggering markets."
export const MUTE_HINT = "Bench this wallet — its entries stop triggering markets."

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
