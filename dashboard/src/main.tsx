import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { RouterProvider } from "react-router-dom"

import { router } from "./app"
import "./index.css"

const REFETCH_FALLBACK_MS = 30_000

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchInterval: REFETCH_FALLBACK_MS, staleTime: 5_000, retry: 1 },
  },
})

const root = document.getElementById("root")
if (!root) throw new Error("missing #root element")

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
