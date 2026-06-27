import { describe, expect, it } from "vitest"

import { arbKindLabel, basketCost, rankByEv, returnOnCost } from "./arb"
import type { ArbItem } from "./types"

const arb = (overrides: Partial<ArbItem> = {}): ArbItem => ({
  id: "a1",
  event_slug: "evt",
  kind: "dutch_book",
  ev: 0.1,
  legs: [
    { market_id: "m1", side: "YES", price: 0.3 },
    { market_id: "m2", side: "YES", price: 0.3 },
    { market_id: "m3", side: "YES", price: 0.3 },
  ],
  ...overrides,
})

describe("arbKindLabel", () => {
  it("maps known kinds to readable labels", () => {
    expect(arbKindLabel("dutch_book")).toBe("Dutch book")
    expect(arbKindLabel("ladder_spread")).toBe("Ladder spread")
  })

  it("falls back to the raw kind when unknown", () => {
    expect(arbKindLabel("mystery")).toBe("mystery")
  })
})

describe("basketCost", () => {
  it("sums the leg prices", () => {
    expect(basketCost(arb())).toBeCloseTo(0.9)
  })
})

describe("returnOnCost", () => {
  it("is ev over the basket cost", () => {
    expect(returnOnCost(arb())).toBeCloseTo(0.1 / 0.9)
  })

  it("is zero when the basket has no cost", () => {
    expect(returnOnCost(arb({ legs: [] }))).toBe(0)
  })
})

describe("rankByEv", () => {
  it("orders baskets by descending EV without mutating the input", () => {
    const low = arb({ id: "low", ev: 0.05 })
    const high = arb({ id: "high", ev: 0.2 })
    const input = [low, high]
    expect(rankByEv(input).map((item) => item.id)).toEqual(["high", "low"])
    expect(input.map((item) => item.id)).toEqual(["low", "high"])
  })
})
