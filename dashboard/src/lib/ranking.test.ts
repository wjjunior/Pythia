import { describe, expect, it } from "vitest"

import { calibrationVerdict, cohortConfidence, COHORT_MIN_N, exposureSeverity } from "./ranking"
import type { CategoryCalibration } from "./types"

const category = (delta: number): CategoryCalibration => ({
  category: "c",
  brier_system: 0.2,
  brier_market: 0.2 + delta,
  delta,
  n: 10,
  invalid_count: 0,
  invalid_rate: 0,
})

describe("calibrationVerdict", () => {
  it("reads the delta sign (positive ⇒ system better)", () => {
    expect(calibrationVerdict(category(0.05))).toBe("system")
    expect(calibrationVerdict(category(-0.05))).toBe("market")
    expect(calibrationVerdict(category(0))).toBe("tie")
  })

  it("works for any item carrying a delta (cohorts reuse it)", () => {
    expect(calibrationVerdict({ delta: 0.1 })).toBe("system")
    expect(calibrationVerdict({ delta: -0.1 })).toBe("market")
  })
})

describe("cohortConfidence", () => {
  it("flags low confidence below the min-n threshold", () => {
    expect(cohortConfidence(COHORT_MIN_N - 1)).toBe("low")
    expect(cohortConfidence(COHORT_MIN_N)).toBe("ok")
    expect(cohortConfidence(COHORT_MIN_N + 50)).toBe("ok")
  })
})

describe("exposureSeverity", () => {
  it("escalates with the exposed fraction", () => {
    expect(exposureSeverity(0.3)).toBe("ok")
    expect(exposureSeverity(0.7)).toBe("warn")
    expect(exposureSeverity(0.95)).toBe("high")
  })
})
