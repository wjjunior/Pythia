// The canonical Polymarket page for a market is its event page; the slug crosses the API as data.
const POLYMARKET_EVENT_BASE = "https://polymarket.com/event"

export const polymarketUrl = (eventSlug: string | null | undefined): string | null =>
  eventSlug ? `${POLYMARKET_EVENT_BASE}/${eventSlug}` : null
