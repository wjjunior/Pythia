// Thin typed REST client. The REST snapshot is the source of truth; SSE is advisory.
import type {
  CalibrationResponse,
  CostResponse,
  EstimateDetail,
  Market,
  MarketDetail,
  PaperPortfolio,
  PortfolioResponse,
  RadarItem,
  ReviewRequest,
  ReviewResult,
  SimulationResponse,
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
  calibration: (category?: string) => get<CalibrationResponse>("/calibration", { category }),
  cost: () => get<CostResponse>("/cost"),
  simulation: (category?: string) => get<SimulationResponse>("/simulation", { category }),
  review: (estimateId: string, body: ReviewRequest) =>
    request<ReviewResult>(withParams(`/review/${estimateId}`), {
      method: "POST",
      body: JSON.stringify(body),
    }),
}
