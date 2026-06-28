import { describe, expect, it } from "vitest"

import { arbKindLabel, basketCost, capitalFit, legsByPrice, rankByEv, returnOnCost } from "./arb"
import type { ArbItem, ArbLegItem } from "./types"

const leg = (overrides: Partial<ArbLegItem> = {}): ArbLegItem => ({
  market_id: "m",
  side: "YES",
  price: 0.3,
  question: "q",
  ...overrides,
})

const arb = (overrides: Partial<ArbItem> = {}): ArbItem => ({
  id: "a1",
  event_slug: "evt",
  kind: "dutch_book",
  ev: 0.1,
  legs: [leg({ market_id: "m1" }), leg({ market_id: "m2" }), leg({ market_id: "m3" })],
  detected_at: null,
  approved: false,
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

describe("legsByPrice", () => {
  it("orders legs by descending price without mutating the input", () => {
    const input = [leg({ market_id: "lo", price: 0.1 }), leg({ market_id: "hi", price: 0.5 })]
    expect(legsByPrice(input).map((l) => l.market_id)).toEqual(["hi", "lo"])
    expect(input.map((l) => l.market_id)).toEqual(["lo", "hi"])
  })
})

describe("capitalFit", () => {
  it("flags a cost above available capital", () => {
    expect(capitalFit(9.65, 100)).toBe("ok")
    expect(capitalFit(150, 100)).toBe("exceeds")
  })
})
