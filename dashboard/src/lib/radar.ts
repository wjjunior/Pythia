// Pure derived state for the opportunity radar. Business logic here, never in components.
import {
  CALIBRATION_MAX_DAYS_TO_RESOLVE,
  CALIBRATION_MIN_WIN_MULTIPLE,
  CALIBRATION_MIN_WIN_PROBABILITY,
} from "./ranking"
import type { RadarItem, ReviewStatus } from "./types"

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

// The risk gate already chose the size; the operator only approves. A vetoed or never-evaluated
// opportunity has suggested_size 0, and an already-acted-on one cannot be re-approved.
export const isApprovable = (item: RadarItem): boolean =>
  item.suggested_size > 0 && item.review_status === "none"

export const discardedCount = (items: RadarItem[]): number =>
  items.filter((item) => item.review_status === "rejected").length

// Discarded = rejected via the review gate. Hidden by default so the queue stays the live work list.
export const visibleRadar = (items: RadarItem[], showDiscarded: boolean): RadarItem[] =>
  showDiscarded ? items : items.filter((item) => item.review_status !== "rejected")

export type RadarSortKey =
  | "market"
  | "category"
  | "side"
  | "resolves_in"
  | "closes_in"
  | "p_model"
  | "entry"
  | "win"
  | "win_prob"
  | "net_edge"
  | "score"
  | "status"

export type SortDir = "asc" | "desc"

export interface RadarColumn {
  key: RadarSortKey
  label: string
  hint: string
  numeric: boolean
}

// One source for the header label, its hover definition, and its sort key — drives both the table
// header and sortRadar, so a new column can't drift between the two.
export const RADAR_COLUMNS: RadarColumn[] = [
  { key: "market", label: "Market", hint: "The market question, as listed on Polymarket.", numeric: false },
  { key: "category", label: "Category", hint: "Market category — calibration is measured per category.", numeric: false },
  { key: "side", label: "Side", hint: "The side the model recommends (YES or NO).", numeric: false },
  { key: "resolves_in", label: "resolves in", hint: "Days until the market resolves (UMA settlement) — sooner means faster calibration feedback.", numeric: true },
  { key: "closes_in", label: "closes in", hint: "Days until trading closes on the venue.", numeric: true },
  { key: "p_model", label: "p_model", hint: "Model probability for the recommended side.", numeric: true },
  { key: "entry", label: "entry", hint: "Price you'd pay to enter the recommended side (its spread-adjusted ask).", numeric: true },
  { key: "win", label: "win", hint: "Payout multiple if the position wins ($1 ÷ entry price).", numeric: true },
  { key: "win_prob", label: "win prob", hint: "Model probability that the position wins.", numeric: true },
  { key: "net_edge", label: "net edge", hint: "Model edge after costs — p_win − entry, net of fees and slippage.", numeric: true },
  { key: "score", label: "score", hint: "Opportunity score — the radar's ranking metric (edge weighted by the confidence factors).", numeric: true },
  { key: "status", label: "status", hint: "Whether this opportunity already has a paper position, resolved, or was discarded.", numeric: false },
]

const sortValue: Record<RadarSortKey, (item: RadarItem) => string | number | null> = {
  market: (item) => item.market.question,
  category: (item) => item.market.category,
  side: (item) => item.recommended_side,
  resolves_in: (item) => item.resolves_in_days,
  closes_in: (item) => item.closes_in_days,
  p_model: (item) => item.estimate.p_model,
  entry: (item) => item.entry_price,
  win: (item) => item.win_multiple,
  win_prob: (item) => item.win_probability,
  net_edge: (item) => item.score.edge_net,
  score: (item) => item.score.opportunity_score,
  status: (item) => item.review_status,
}

// A stable copy sorted by one column; null-valued rows (e.g. no resolution date) stay last in both
// directions, so changing the sort never buries them at the top.
export const sortRadar = (items: RadarItem[], key: RadarSortKey, dir: SortDir): RadarItem[] => {
  const read = sortValue[key]
  const factor = dir === "asc" ? 1 : -1
  return [...items].sort((a, b) => {
    const va = read(a)
    const vb = read(b)
    if (va === null && vb === null) return 0
    if (va === null) return 1
    if (vb === null) return -1
    const cmp =
      typeof va === "string" ? va.localeCompare(vb as string) : (va as number) - (vb as number)
    return cmp * factor
  })
}
