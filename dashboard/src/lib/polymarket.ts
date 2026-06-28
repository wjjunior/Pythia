// The canonical Polymarket page for a market is its event page; the slug crosses the API as data.
const POLYMARKET_EVENT_BASE = "https://polymarket.com/event"

export const polymarketUrl = (eventSlug: string | null | undefined): string | null =>
  eventSlug ? `${POLYMARKET_EVENT_BASE}/${eventSlug}` : null

// A tracked trader's external analytics page, keyed by wallet address.
const TRADER_ANALYTICS_BASE = "https://polymarketanalytics.com/traders"

export const traderUrl = (wallet: string | null | undefined): string | null =>
  wallet ? `${TRADER_ANALYTICS_BASE}/${wallet}` : null
