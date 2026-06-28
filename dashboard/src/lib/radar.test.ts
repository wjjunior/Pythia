import { describe, expect, it } from "vitest"

import { DEFAULT_RADAR_FILTERS, reviewStatusBadge } from "./radar"

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
