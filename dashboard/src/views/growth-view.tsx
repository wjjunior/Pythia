import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { QueryState } from "@/components/async-boundary"
import { PageHeader } from "@/components/page"
import { Stat } from "@/components/stat"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { usePortfolio, useSimulation } from "@/hooks/use-api"
import { num, pct, signedNum, usd } from "@/lib/format"

const RUIN_WARN = 0.1

export function GrowthView() {
  const simulation = useSimulation()
  const portfolio = usePortfolio()

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Growth projection"
        description="Monte Carlo over the measured calibration distribution. A positive edge does not guarantee survival."
      />
      <QueryState query={simulation}>
        {(sim) => {
          const curve = sim.growth_curve.map((point) => ({
            kelly: point.kelly_mult,
            growth: point.log_growth_rate,
          }))
          const ruinVariant = sim.probability_of_ruin > RUIN_WARN ? "negative" : "positive"
          return (
            <div className="flex flex-col gap-4">
              <Card>
                <CardContent className="flex flex-wrap gap-8 pt-5">
                  <Stat
                    label="Available capital"
                    value={
                      portfolio.data ? usd(portfolio.data.capital.available_capital) : "—"
                    }
                    sub="from /api/portfolio"
                  />
                  <Stat label="Starting bankroll" value={usd(sim.starting_bankroll)} />
                  <Stat
                    label="Log-growth / step"
                    value={
                      <span className={sim.log_growth_rate >= 0 ? "text-positive" : "text-negative"}>
                        {signedNum(sim.log_growth_rate, 5)}
                      </span>
                    }
                  />
                  <Stat label="Sample size n" value={sim.n} sub={`${sim.paths} paths · ${sim.horizon} steps`} />
                </CardContent>
              </Card>

              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                <Card>
                  <CardContent className="pt-5">
                    <Stat label="Terminal P5" value={usd(sim.terminal_p5)} />
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <Stat label="Terminal P50" value={usd(sim.terminal_p50)} />
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <Stat label="Terminal P95" value={usd(sim.terminal_p95)} />
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <Stat
                      label="Prob. of ruin"
                      value={pct(sim.probability_of_ruin)}
                      sub={
                        <Badge variant={ruinVariant}>floor {pct(sim.ruin_floor, 0)} of bankroll</Badge>
                      }
                    />
                  </CardContent>
                </Card>
              </div>

              <div className="grid gap-4 lg:grid-cols-3">
                <Card className="lg:col-span-2">
                  <CardHeader>
                    <CardTitle>Expected log-growth × Kelly multiplier</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <ResponsiveContainer width="100%" height={260}>
                      <LineChart data={curve} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                        <XAxis
                          dataKey="kelly"
                          type="number"
                          domain={["dataMin", "dataMax"]}
                          stroke="var(--color-muted-foreground)"
                          fontSize={12}
                        />
                        <YAxis
                          stroke="var(--color-muted-foreground)"
                          fontSize={12}
                          tickFormatter={(value: number) => num(value, 4)}
                        />
                        <Tooltip formatter={(value: number) => signedNum(value, 5)} />
                        <ReferenceLine y={0} stroke="var(--color-muted-foreground)" />
                        <Line
                          type="monotone"
                          dataKey="growth"
                          stroke="var(--color-chart-1)"
                          strokeWidth={2}
                          dot
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>Drawdown</CardTitle>
                  </CardHeader>
                  <CardContent className="flex flex-col gap-6 pt-1">
                    <Stat label="Mean max drawdown" value={pct(sim.mean_max_drawdown)} />
                    <Stat label="P95 max drawdown" value={pct(sim.p95_max_drawdown)} />
                  </CardContent>
                </Card>
              </div>
            </div>
          )
        }}
      </QueryState>
    </div>
  )
}
