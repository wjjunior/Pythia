import { describe, expect, it } from "vitest"

import {
  DEFAULT_RADAR_FILTERS,
  discardedCount,
  isApprovable,
  RADAR_COLUMNS,
  reviewStatusBadge,
  sortRadar,
  visibleRadar,
} from "./radar"
import type { RadarItem, ReviewStatus, Side } from "./types"

function radarItem(
  over: Partial<{
    id: string
    question: string
    category: string
    side: Side
    resolvesIn: number | null
    closesIn: number | null
    pModel: number
    entry: number
    win: number
    winProb: number
    netEdge: number
    score: number
    status: ReviewStatus
    suggestedSize: number
  }> = {},
): RadarItem {
  return {
    market: {
      id: over.id ?? over.question ?? "m",
      question: over.question ?? "q",
      category: over.category ?? "c",
      event_slug: "e",
    },
    estimate: { p_model: over.pModel ?? 0.5 },
    score: { edge_net: over.netEdge ?? 0, opportunity_score: over.score ?? 0 },
    recommended_side: over.side ?? "YES",
    entry_price: over.entry ?? 0.5,
    win_multiple: over.win ?? 2,
    win_probability: over.winProb ?? 0.5,
    closes_in_days: over.closesIn === undefined ? 1 : over.closesIn,
    resolves_in_days: over.resolvesIn === undefined ? 1 : over.resolvesIn,
    review_status: over.status ?? "none",
    suggested_size: over.suggestedSize ?? 0,
  } as unknown as RadarItem
}

const ids = (items: RadarItem[]) => items.map((i) => i.market.id)

describe("reviewStatusBadge", () => {
  it("maps acted-on states to badges and leaves untouched ones null", () => {
    expect(reviewStatusBadge("open")).toEqual({ label: "position open", variant: "positive" })
    expect(reviewStatusBadge("resolved")).toEqual({ label: "resolved", variant: "muted" })
    expect(reviewStatusBadge("rejected")).toEqual({ label: "rejected", variant: "negative" })
    expect(reviewStatusBadge("none")).toBeNull()
  })
})

describe("DEFAULT_RADAR_FILTERS", () => {
  it("seeds the filter bar from the calibration thresholds", () => {
    expect(DEFAULT_RADAR_FILTERS.maxDaysToResolve).toBeGreaterThan(0)
    expect(DEFAULT_RADAR_FILTERS.minWinProbability).toBeGreaterThan(0)
    expect(DEFAULT_RADAR_FILTERS.minWinMultiple).toBeGreaterThanOrEqual(1)
  })
})

describe("isApprovable", () => {
  it("is true only for an untouched opportunity with a positive risk-approved size", () => {
    expect(isApprovable(radarItem({ suggestedSize: 12.5, status: "none" }))).toBe(true)
  })

  it("is false when the risk gate left no size (vetoed or never evaluated)", () => {
    expect(isApprovable(radarItem({ suggestedSize: 0, status: "none" }))).toBe(false)
  })

  it("is false once the opportunity has already been acted on", () => {
    expect(isApprovable(radarItem({ suggestedSize: 12.5, status: "open" }))).toBe(false)
    expect(isApprovable(radarItem({ suggestedSize: 12.5, status: "rejected" }))).toBe(false)
  })
})

describe("visibleRadar", () => {
  const items = [
    radarItem({ id: "a", status: "none" }),
    radarItem({ id: "b", status: "rejected" }),
    radarItem({ id: "c", status: "open" }),
  ]

  it("hides discarded (rejected) rows by default", () => {
    expect(ids(visibleRadar(items, false))).toEqual(["a", "c"])
  })

  it("reveals discarded rows when asked", () => {
    expect(ids(visibleRadar(items, true))).toEqual(["a", "b", "c"])
  })
})

describe("discardedCount", () => {
  it("counts the rejected rows so the toggle can label them", () => {
    const items = [
      radarItem({ status: "rejected" }),
      radarItem({ status: "none" }),
      radarItem({ status: "rejected" }),
    ]
    expect(discardedCount(items)).toBe(2)
  })
})

describe("sortRadar", () => {
  it("sorts a numeric column descending and ascending without mutating the input", () => {
    const items = [
      radarItem({ id: "lo", score: 0.1 }),
      radarItem({ id: "hi", score: 0.9 }),
      radarItem({ id: "mid", score: 0.5 }),
    ]
    expect(ids(sortRadar(items, "score", "desc"))).toEqual(["hi", "mid", "lo"])
    expect(ids(sortRadar(items, "score", "asc"))).toEqual(["lo", "mid", "hi"])
    expect(ids(items)).toEqual(["lo", "hi", "mid"]) // original untouched
  })

  it("sorts a string column case-insensitively", () => {
    const items = [
      radarItem({ id: "1", question: "Zebra race" }),
      radarItem({ id: "2", question: "apple vote" }),
    ]
    expect(ids(sortRadar(items, "market", "asc"))).toEqual(["2", "1"])
  })

  it("keeps null-valued rows last in both directions", () => {
    const items = [
      radarItem({ id: "soon", resolvesIn: 2 }),
      radarItem({ id: "none", resolvesIn: null }),
      radarItem({ id: "later", resolvesIn: 9 }),
    ]
    expect(ids(sortRadar(items, "resolves_in", "asc"))).toEqual(["soon", "later", "none"])
    expect(ids(sortRadar(items, "resolves_in", "desc"))).toEqual(["later", "soon", "none"])
  })
})

describe("RADAR_COLUMNS", () => {
  it("gives every column a non-empty label and hint", () => {
    expect(RADAR_COLUMNS.length).toBeGreaterThan(0)
    for (const column of RADAR_COLUMNS) {
      expect(column.label.length).toBeGreaterThan(0)
      expect(column.hint.length).toBeGreaterThan(0)
    }
  })
})
