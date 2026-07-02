// Thin typed REST client. The REST snapshot is the source of truth; SSE is advisory.
import type {
  ArbApproveResult,
  ArbItem,
  CalibrationResponse,
  CandidateFeedItem,
  CostResponse,
  EstimateDetail,
  Market,
  MarketDetail,
  OverrideAction,
  PaperPortfolio,
  PaperTrade,
  PortfolioResponse,
  RadarItem,
  ReviewRequest,
  ReviewResult,
  RosterEntry,
  RosterEventItem,
  SimulationResponse,
  TraderPositionItem,
} from "./types"

const BASE = "/api"

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

type Params = Record<string, string | number | undefined>

function withParams(path: string, params?: Params): string {
  if (!params) return `${BASE}${path}`
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value))
  }
  const query = search.toString()
  return query ? `${BASE}${path}?${query}` : `${BASE}${path}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  })
  if (!res.ok) {
    throw new ApiError(res.status, `${init?.method ?? "GET"} ${path} → ${res.status}`)
  }
  return (await res.json()) as T
}

const get = <T>(path: string, params?: Params): Promise<T> => request<T>(withParams(path, params))

export const api = {
  radar: (
    params: {
      maxDaysToResolve?: number
      minWinMultiple?: number
      minWinProbability?: number
      limit?: number
    } = {},
  ) =>
    get<RadarItem[]>("/radar", {
      limit: params.limit ?? 50,
      max_days_to_resolve: params.maxDaysToResolve,
      min_win_multiple: params.minWinMultiple,
      min_win_probability: params.minWinProbability,
    }),
  markets: (category?: string) => get<Market[]>("/markets", { category }),
  market: (id: string) => get<MarketDetail>(`/markets/${id}`),
  estimate: (id: string) => get<EstimateDetail>(`/estimates/${id}`),
  portfolio: () => get<PortfolioResponse>("/portfolio"),
  paper: () => get<PaperPortfolio>("/paper/positions"),
  closePosition: (tradeId: string) =>
    request<PaperTrade>(withParams(`/paper/positions/${tradeId}/close`), { method: "POST" }),
  calibration: (category?: string) => get<CalibrationResponse>("/calibration", { category }),
  cost: () => get<CostResponse>("/cost"),
  arbs: () => get<ArbItem[]>("/arbs"),
  approveArb: (arbId: string, baskets: number) =>
    request<ArbApproveResult>(withParams(`/arbs/${arbId}/approve`), {
      method: "POST",
      body: JSON.stringify({ baskets }),
    }),
  simulation: (category?: string) => get<SimulationResponse>("/simulation", { category }),
  review: (estimateId: string, body: ReviewRequest) =>
    request<ReviewResult>(withParams(`/review/${estimateId}`), {
      method: "POST",
      body: JSON.stringify(body),
    }),
  roster: () => get<RosterEntry[]>("/smartmoney/roster"),
  smartCandidates: (limit = 50) =>
    get<CandidateFeedItem[]>("/smartmoney/candidates", { limit }),
  rosterEvents: (limit = 50) => get<RosterEventItem[]>("/smartmoney/roster-events", { limit }),
  positions: () => get<TraderPositionItem[]>("/smartmoney/positions"),
  override: (traderId: string, action: OverrideAction) =>
    request<RosterEntry>(withParams(`/smartmoney/traders/${traderId}/override`), {
      method: "POST",
      body: JSON.stringify({ action }),
    }),
}
