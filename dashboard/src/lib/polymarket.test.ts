import { describe, expect, it } from "vitest"

import { polymarketUrl, traderUrl } from "./polymarket"

describe("polymarketUrl", () => {
  it("builds the event page URL from a slug", () => {
    expect(polymarketUrl("openai-ipo-closing-market-cap")).toBe(
      "https://polymarket.com/event/openai-ipo-closing-market-cap",
    )
  })

  it("returns null when there is no slug", () => {
    expect(polymarketUrl(null)).toBeNull()
    expect(polymarketUrl("")).toBeNull()
  })
})

describe("traderUrl", () => {
  it("builds the analytics URL from a wallet", () => {
    expect(traderUrl("0x000d257d2dc7616feaef4ae0f14600fdf50a758e")).toBe(
      "https://polymarketanalytics.com/traders/0x000d257d2dc7616feaef4ae0f14600fdf50a758e",
    )
  })

  it("returns null without a wallet", () => {
    expect(traderUrl(null)).toBeNull()
    expect(traderUrl("")).toBeNull()
  })
})
