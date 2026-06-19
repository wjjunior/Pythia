import { Activity, BarChart3, DollarSign, Radar, TrendingUp, Wallet } from "lucide-react"
import { NavLink, Outlet } from "react-router-dom"

import { useSse } from "@/hooks/use-sse"
import { cn } from "@/lib/utils"

const NAV = [
  { to: "/", label: "Radar", icon: Radar, end: true },
  { to: "/paper", label: "Paper", icon: Wallet, end: false },
  { to: "/calibration", label: "Calibration", icon: BarChart3, end: false },
  { to: "/cost", label: "Cost", icon: DollarSign, end: false },
  { to: "/growth", label: "Growth", icon: TrendingUp, end: false },
]

const STATUS_LABEL = { connecting: "connecting", live: "live", offline: "offline" } as const
const STATUS_DOT = {
  connecting: "bg-muted-foreground",
  live: "bg-positive",
  offline: "bg-negative",
} as const

export function AppShell() {
  const sse = useSse()
  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-3">
          <span className="text-sm font-semibold tracking-tight">Pythia</span>
          <nav className="flex items-center gap-1">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors",
                    isActive
                      ? "bg-secondary text-secondary-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )
                }
              >
                <Icon className="h-4 w-4" />
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
            <Activity className="h-3.5 w-3.5" />
            <span className={cn("h-2 w-2 rounded-full", STATUS_DOT[sse])} />
            {STATUS_LABEL[sse]}
          </div>
        </div>
      </header>
      <main className="mx-auto flex max-w-7xl flex-col gap-6 px-6 py-6">
        <Outlet />
      </main>
    </div>
  )
}
