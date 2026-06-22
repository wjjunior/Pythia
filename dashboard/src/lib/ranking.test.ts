import { describe, expect, it } from "vitest"

import {
  calibrationVerdict,
  exposureSeverity,
  impliedSide,
  marketPrice,
  rankByOpportunity,
} from "./ranking"
import type { CategoryCalibration, RadarItem, Score } from "./types"

const score = (over: Partial<Score> = {}): Score => ({
  estimate_id: "e",
  edge: 0.1,
  edge_net: 0.09,
  ev_unit: 0.09,
  p_resol: 1,
  p_liq: 1,
  p_time: 1,
  p_corr: 1,
  opportunity_score: 0.05,
  kelly_fraction: 0.2,
  ...over,
})

const item = (opportunity: number, scoreOver: Partial<Score> = {}): RadarItem => ({
  market: {
    id: `m${opportunity}`,
    external_id: "x",
    question: "q",
    category: "c",
    resolution_rule: "r",
    resolved: false,
  },
  estimate: {
    id: "e",
    market_id: "m",
    snapshot_id: "s",
    p_model: 0.6,
    confidence: 0.5,
    lower: 0.5,
    upper: 0.7,
    rationale: "r",
    evidence_ids: [],
    model_name: "stub",
  },
  score: score({ opportunity_score: opportunity, ...scoreOver }),
})

const category = (delta: number): CategoryCalibration => ({
  category: "c",
  brier_system: 0.2,
  brier_market: 0.2 + delta,
  delta,
  n: 10,
  invalid_count: 0,
  invalid_rate: 0,
})

describe("rankByOpportunity", () => {
  it("orders by opportunity_score descending without mutating the input", () => {
    const input = [item(0.1), item(0.5), item(0.3)]
    const ranked = rankByOpportunity(input)
    expect(ranked.map((i) => i.score.opportunity_score)).toEqual([0.5, 0.3, 0.1])
    expect(input.map((i) => i.score.opportunity_score)).toEqual([0.1, 0.5, 0.3])
  })
})

describe("impliedSide", () => {
  it("backs YES on a non-negative edge and NO otherwise", () => {
    expect(impliedSide(score({ edge: 0.1 }))).toBe("YES")
    expect(impliedSide(score({ edge: 0 }))).toBe("YES")
    expect(impliedSide(score({ edge: -0.1 }))).toBe("NO")
  })
})

describe("marketPrice", () => {
  it("reconstructs q = p_model − edge", () => {
    expect(marketPrice(item(0.05, { edge: 0.1 }))).toBeCloseTo(0.5)
  })
})

describe("calibrationVerdict", () => {
  it("reads the delta sign (positive ⇒ system better)", () => {
    expect(calibrationVerdict(category(0.05))).toBe("system")
    expect(calibrationVerdict(category(-0.05))).toBe("market")
    expect(calibrationVerdict(category(0))).toBe("tie")
  })
})

describe("exposureSeverity", () => {
  it("escalates with the exposed fraction", () => {
    expect(exposureSeverity(0.3)).toBe("ok")
    expect(exposureSeverity(0.7)).toBe("warn")
    expect(exposureSeverity(0.95)).toBe("high")
  })
})
