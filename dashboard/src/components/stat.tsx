import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

interface StatProps {
  label: ReactNode
  value: ReactNode
  sub?: ReactNode
  className?: string
}

export function Stat({ label, value, sub, className }: StatProps) {
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <span className="text-xs uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="text-2xl font-semibold tabular-nums">{value}</span>
      {sub ? <span className="text-xs text-muted-foreground">{sub}</span> : null}
    </div>
  )
}
