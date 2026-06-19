import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { EmptyState, QueryState } from "@/components/async-boundary"
import { PageHeader } from "@/components/page"
import { Stat } from "@/components/stat"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useCalibration } from "@/hooks/use-api"
import { num, pct, signedNum } from "@/lib/format"
import { calibrationVerdict } from "@/lib/ranking"
import type { ReliabilityBin } from "@/lib/types"

const VERDICT = {
  system: { label: "system better", variant: "positive" as const },
  market: { label: "market better", variant: "negative" as const },
  tie: { label: "tie", variant: "muted" as const },
}

function reliabilityData(bins: ReliabilityBin[]) {
  return bins
    .filter((bin) => bin.count > 0)
    .map((bin) => ({
      forecast: bin.mean_forecast,
      observed: bin.observed_frequency,
      ideal: bin.mean_forecast,
    }))
}

export function CalibrationView() {
  const calibration = useCalibration()

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Calibration"
        description="The headline: is the system better calibrated than the market? Brier is lower-is-better; n always shown."
      />
      <QueryState query={calibration}>
        {(report) => {
          const reliability = reliabilityData(report.reliability)
          return (
            <div className="flex flex-col gap-4">
              <Card>
                <CardHeader>
                  <CardTitle>Brier by category</CardTitle>
                </CardHeader>
                <CardContent>
                  {report.categories.length === 0 ? (
                    <EmptyState message="No resolved markets to score yet." />
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Category</TableHead>
                          <TableHead className="text-right">n</TableHead>
                          <TableHead className="text-right">Brier(system)</TableHead>
                          <TableHead className="text-right">Brier(market)</TableHead>
                          <TableHead className="text-right">Δ</TableHead>
                          <TableHead>Verdict</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {report.categories.map((category) => {
                          const verdict = VERDICT[calibrationVerdict(category)]
                          return (
                            <TableRow key={category.category}>
                              <TableCell className="font-medium">{category.category}</TableCell>
                              <TableCell className="text-right tabular-nums">{category.n}</TableCell>
                              <TableCell className="text-right tabular-nums">
                                {num(category.brier_system)}
                              </TableCell>
                              <TableCell className="text-right tabular-nums text-muted-foreground">
                                {num(category.brier_market)}
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {signedNum(category.delta)}
                              </TableCell>
                              <TableCell>
                                <Badge variant={verdict.variant}>{verdict.label}</Badge>
                              </TableCell>
                            </TableRow>
                          )
                        })}
                      </TableBody>
                    </Table>
                  )}
                </CardContent>
              </Card>

              <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                  <CardHeader>
                    <CardTitle>Reliability curve</CardTitle>
                  </CardHeader>
                  <CardContent>
                    {reliability.length === 0 ? (
                      <EmptyState message="Not enough resolved forecasts." />
                    ) : (
                      <ResponsiveContainer width="100%" height={240}>
                        <LineChart data={reliability} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                          <XAxis
                            dataKey="forecast"
                            type="number"
                            domain={[0, 1]}
                            stroke="var(--color-muted-foreground)"
                            fontSize={12}
                            tickFormatter={(value: number) => pct(value, 0)}
                          />
                          <YAxis
                            domain={[0, 1]}
                            stroke="var(--color-muted-foreground)"
                            fontSize={12}
                            tickFormatter={(value: number) => pct(value, 0)}
                          />
                          <Tooltip formatter={(value: number) => pct(value)} />
                          <Line
                            type="monotone"
                            dataKey="observed"
                            stroke="var(--color-chart-1)"
                            strokeWidth={2}
                            dot
                          />
                          <Line
                            type="monotone"
                            dataKey="ideal"
                            stroke="var(--color-muted-foreground)"
                            strokeDasharray="4 4"
                            dot={false}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    )}
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>EV honesty check</CardTitle>
                  </CardHeader>
                  <CardContent className="flex flex-wrap gap-8 pt-1">
                    {report.ev ? (
                      <>
                        <Stat label="Predicted EV" value={signedNum(report.ev.mean_predicted)} />
                        <Stat label="Realized EV" value={signedNum(report.ev.mean_realized)} />
                        <Stat label="n" value={report.ev.n} />
                      </>
                    ) : (
                      <EmptyState message="No resolved paper trades yet." />
                    )}
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
