import { describe, expect, it } from "vitest"

import {
  daysToClose,
  isoDate,
  isoDateTime,
  multiple,
  pct,
  signedNum,
  signedPct,
  usd,
} from "./format"

describe("format", () => {
  it("scales fractions to percent", () => {
    expect(pct(0.1234)).toBe("12.3%")
    expect(pct(0.5, 0)).toBe("50%")
  })

  it("prefixes signed percentages", () => {
    expect(signedPct(0.05)).toBe("+5.0%")
    expect(signedPct(-0.05)).toBe("-5.0%")
  })

  it("formats usd with grouping", () => {
    expect(usd(1234.5)).toBe("$1,234.50")
  })

  it("prefixes signed numbers", () => {
    expect(signedNum(0.5, 2)).toBe("+0.50")
    expect(signedNum(-0.5, 2)).toBe("-0.50")
  })

  it("renders dates in UTC", () => {
    expect(isoDate("2026-03-01T12:00:00Z")).toBe("2026-03-01")
    expect(isoDateTime("2026-03-01T12:30:00Z")).toBe("2026-03-01 12:30")
  })

  it("renders payout multiples", () => {
    expect(multiple(2.5)).toBe("2.50×")
    expect(multiple(1.4)).toBe("1.40×")
  })

  it("renders days-to-close, flooring and guarding null/sub-day", () => {
    expect(daysToClose(3.7)).toBe("3d")
    expect(daysToClose(0.5)).toBe("<1d")
    expect(daysToClose(null)).toBe("—")
  })
})
