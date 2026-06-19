// SSE channels (mirror of pythia.api.events.SseChannel). Payloads are advisory: they invalidate the
// matching TanStack Query keys; the REST snapshot remains the source of truth.

export const SSE_URL = "/api/events"

export const SSE_CHANNELS = ["radar_changed", "paper_changed", "calibration_changed"] as const
export type SseChannel = (typeof SSE_CHANNELS)[number]

// Which query-key prefixes each channel invalidates.
export const SSE_INVALIDATIONS: Record<SseChannel, string[][]> = {
  radar_changed: [["radar"], ["market"], ["markets"]],
  paper_changed: [["portfolio"], ["paper"], ["cost"]],
  calibration_changed: [["calibration"], ["simulation"]],
}
