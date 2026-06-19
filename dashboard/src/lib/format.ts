// Pure display formatters. UTC-stable dates keep the legibility surface reproducible.

export const pct = (x: number, digits = 1): string => `${(x * 100).toFixed(digits)}%`

export const signedPct = (x: number, digits = 1): string =>
  `${x >= 0 ? "+" : ""}${(x * 100).toFixed(digits)}%`

export const usd = (x: number, digits = 2): string =>
  `$${x.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`

export const usdCompact = (x: number): string =>
  `$${x.toLocaleString("en-US", { maximumFractionDigits: 0 })}`

export const num = (x: number, digits = 3): string => x.toFixed(digits)

export const signedNum = (x: number, digits = 3): string => `${x >= 0 ? "+" : ""}${x.toFixed(digits)}`

export const isoDate = (iso: string): string => new Date(iso).toISOString().slice(0, 10)

export const isoDateTime = (iso: string): string =>
  new Date(iso).toISOString().slice(0, 16).replace("T", " ")
