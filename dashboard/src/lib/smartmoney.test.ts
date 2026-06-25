import { describe, expect, it } from "vitest"

import { rankRoster, shortWallet } from "./smartmoney"
import type { RosterEntry, ScorecardView, TraderState } from "./types"

const card = (trader_score: number | null): ScorecardView => ({
  asof: "2026-01-01T00:00:00Z",
  n_resolved: 10,
  market_class_ok: true,
  trader_score,
  roi: 0.1,
  risk_adj_edge: 0.1,
  sortino: 1,
  skew: 0,
  max_drawdown: 0.1,
  agree_rate: 0.5,
  agreed_bpd: 0.01,
})

const entry = (id: string, state: TraderState, score: number | null): RosterEntry => ({
  trader: {
    id,
    wallet: `0x${id}`,
    state,
    added_at: "2026-01-01T00:00:00Z",
    state_since: "2026-01-01T00:00:00Z",
  },
  scorecard: score === undefined ? null : card(score),
  last_entry: null,
  reason: null,
})

describe("rankRoster", () => {
  it("orders by state (active first) then by trader_score descending", () => {
    const ranked = rankRoster([
      entry("a", "benched", 0.9),
      entry("b", "active", 0.2),
      entry("c", "active", 0.8),
      entry("d", "trial", 0.5),
    ])
    expect(ranked.map((e) => e.trader.id)).toEqual(["c", "b", "d", "a"])
  })

  it("sorts a missing scorecard last within its state", () => {
    const ranked = rankRoster([
      { ...entry("x", "active", 0.1), scorecard: null },
      entry("y", "active", 0.4),
    ])
    expect(ranked.map((e) => e.trader.id)).toEqual(["y", "x"])
  })
})

describe("shortWallet", () => {
  it("truncates long addresses and leaves short ones intact", () => {
    expect(shortWallet("0x1234567890abcdef")).toBe("0x1234…cdef")
    expect(shortWallet("0xabcd")).toBe("0xabcd")
  })
})
