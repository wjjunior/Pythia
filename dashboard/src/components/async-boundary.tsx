import type { UseQueryResult } from "@tanstack/react-query"
import type { ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

interface QueryStateProps<T> {
  query: UseQueryResult<T>
  children: (data: T) => ReactNode
  skeleton?: ReactNode
}

export function QueryState<T>({ query, children, skeleton }: QueryStateProps<T>) {
  if (query.isPending) {
    return skeleton ?? <Skeleton className="h-40 w-full" />
  }
  if (query.isError) {
    return (
      <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/40 bg-destructive/5 p-5">
        <p className="text-sm text-destructive">
          {query.error instanceof Error ? query.error.message : "Request failed"}
        </p>
        <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
          Retry
        </Button>
      </div>
    )
  }
  return <>{children(query.data)}</>
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
      {message}
    </div>
  )
}
