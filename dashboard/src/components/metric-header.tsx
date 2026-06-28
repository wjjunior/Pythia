import { cn } from "@/lib/utils"

interface MetricHeaderProps {
  label: string
  hint: string
  className?: string
}

// A column label with its definition on hover — reused across the dense ops tables so every cryptic
// metric is self-explaining. Uses the native title tooltip to avoid a tooltip-library dependency.
export function MetricHeader({ label, hint, className }: MetricHeaderProps) {
  return (
    <span
      title={hint}
      className={cn(
        "cursor-help border-b border-dotted border-muted-foreground/50 decoration-dotted",
        className,
      )}
    >
      {label}
    </span>
  )
}
