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
import {
  calibrationVerdict,
  cohortConfidence,
  COHORT_LABELS,
  COHORT_MIN_N,
  COHORT_SLICES,
  gateBlockedReasons,
  gateState,
  isLowN,
  LOW_N,
  OVERALL_COHORT,
  sortChannelEntries,
} from "@/lib/ranking"
import type { ChannelBrier, CohortCalibration, ReliabilityBin } from "@/lib/types"

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

function ChannelBrierCard({ byChannel }: { byChannel: Record<string, ChannelBrier> }) {
  const rows = sortChannelEntries(Object.entries(byChannel))
  return (
    <Card>
      <CardHeader>
        <CardTitle>Brier by channel</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <EmptyState message="No channel calibration data yet." />
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Channel</TableHead>
                  <TableHead className="text-right">n</TableHead>
                  <TableHead className="text-right">Brier(system)</TableHead>
                  <TableHead className="text-right">Brier(market)</TableHead>
                  <TableHead className="text-right">Δ</TableHead>
                  <TableHead>Verdict</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map(([channel, stats]) => {
                  const low = isLowN(stats.n)
                  const verdict = VERDICT[calibrationVerdict(stats)]
                  const rowClass = low ? "text-muted-foreground" : ""
                  return (
                    <TableRow key={channel} className={rowClass}>
                      <TableCell className="font-medium">{channel}</TableCell>
                      <TableCell className="text-right tabular-nums">{stats.n}</TableCell>
                      <TableCell className="text-right tabular-nums">{num(stats.brier_system)}</TableCell>
                      <TableCell className="text-right tabular-nums">{num(stats.brier_market)}</TableCell>
                      <TableCell className="text-right tabular-nums">{signedNum(stats.delta)}</TableCell>
                      <TableCell>
                        {low ? (
                          <Badge variant="muted">{`low signal (n<${LOW_N})`}</Badge>
                        ) : (
                          <Badge variant={verdict.variant}>{verdict.label}</Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
            <p className="mt-2 text-xs text-muted-foreground">
              Channels with fewer than {LOW_N} resolved markets are greyed out — not enough signal to
              read as a calibration claim.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function CohortBreakdown({ cohorts }: { cohorts: CohortCalibration[] }) {
  const overall = cohorts.find((cohort) => cohort.cohort === OVERALL_COHORT)
  if (!overall) {
    return <EmptyState message="No resolved markets to score yet." />
  }
  const overallVerdict = VERDICT[calibrationVerdict(overall)]
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
        <Stat
          label="Overall · Brier(system)"
          value={num(overall.brier_system)}
          sub={`vs market ${num(overall.brier_market)} · n=${overall.n}`}
        />
        <Stat label="Δ vs market" value={signedNum(overall.delta)} />
        <Badge variant={overallVerdict.variant}>{overallVerdict.label}</Badge>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Cohort slice</TableHead>
            <TableHead className="text-right">n</TableHead>
            <TableHead className="text-right">Brier(system)</TableHead>
            <TableHead className="text-right">Brier(market)</TableHead>
            <TableHead className="text-right">Δ</TableHead>
            <TableHead>Verdict</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {COHORT_SLICES.map((key) => {
            const label = COHORT_LABELS[key] ?? key
            const slice = cohorts.find((cohort) => cohort.cohort === key)
            if (!slice) {
              return (
                <TableRow key={key}>
                  <TableCell className="font-medium">{label}</TableCell>
                  <TableCell colSpan={5} className="text-muted-foreground">
                    no resolved markets in this slice yet
                  </TableCell>
                </TableRow>
              )
            }
            const verdict = VERDICT[calibrationVerdict(slice)]
            return (
              <TableRow key={key}>
                <TableCell className="font-medium">{label}</TableCell>
                <TableCell className="text-right tabular-nums">{slice.n}</TableCell>
                <TableCell className="text-right tabular-nums">{num(slice.brier_system)}</TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {num(slice.brier_market)}
                </TableCell>
                <TableCell className="text-right tabular-nums">{signedNum(slice.delta)}</TableCell>
                <TableCell>
                  {cohortConfidence(slice.n) === "low" ? (
                    <Badge variant="muted">{`low confidence (n<${COHORT_MIN_N})`}</Badge>
                  ) : (
                    <Badge variant={verdict.variant}>{verdict.label}</Badge>
                  )}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
      <p className="text-xs text-muted-foreground">
        Cohorts overlap — a market can be in several, so slices don't sum to overall. Each is a
        labeled subset, never the whole-market calibration claim.
      </p>
    </div>
  )
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
          const gateByCategory = new Map(report.gates.map((gate) => [gate.category, gate]))
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
                          <TableHead className="text-right">invalid-rate</TableHead>
                          <TableHead>Verdict</TableHead>
                          <TableHead>Gate</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {report.categories.map((category) => {
                          const verdict = VERDICT[calibrationVerdict(category)]
                          const gate = gateByCategory.get(category.category)
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
                              <TableCell className="text-right tabular-nums">
                                {pct(category.invalid_rate)}
                              </TableCell>
                              <TableCell>
                                <Badge variant={verdict.variant}>{verdict.label}</Badge>
                              </TableCell>
                              <TableCell>
                                {gateState(gate) === "cleared" ? (
                                  <Badge variant="positive">cleared</Badge>
                                ) : (
                                  <Badge variant="muted" title={gateBlockedReasons(gate)}>
                                    gated
                                  </Badge>
                                )}
                              </TableCell>
                            </TableRow>
                          )
                        })}
                      </TableBody>
                    </Table>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Brier by cohort</CardTitle>
                </CardHeader>
                <CardContent>
                  <CohortBreakdown cohorts={report.cohorts} />
                </CardContent>
              </Card>

              <ChannelBrierCard byChannel={report.by_channel} />

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
