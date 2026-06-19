import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { SSE_CHANNELS, SSE_INVALIDATIONS, SSE_URL } from "@/lib/sse"

export type SseStatus = "connecting" | "live" | "offline"

// Live updates within ~1s: SSE events invalidate the matching query keys. A wide-interval refetch
// (configured on the QueryClient) is the fallback when the stream drops.
export function useSse(): SseStatus {
  const client = useQueryClient()
  const [status, setStatus] = useState<SseStatus>("connecting")

  useEffect(() => {
    const source = new EventSource(SSE_URL)
    source.onopen = () => setStatus("live")
    source.onerror = () => setStatus("offline")

    const listeners = SSE_CHANNELS.map((channel) => {
      const handler = () => {
        setStatus("live")
        for (const queryKey of SSE_INVALIDATIONS[channel]) {
          void client.invalidateQueries({ queryKey })
        }
      }
      source.addEventListener(channel, handler)
      return { channel, handler }
    })

    return () => {
      for (const { channel, handler } of listeners) source.removeEventListener(channel, handler)
      source.close()
    }
  }, [client])

  return status
}
