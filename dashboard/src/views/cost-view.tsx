import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

import { EmptyState, QueryState } from "@/components/async-boundary"
import { PageHeader } from "@/components/page"
import { Stat } from "@/components/stat"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useCost } from "@/hooks/use-api"
import { usd } from "@/lib/format"

function toRows(record: Record<string, number>): { key: string; value: number }[] {
  return Object.entries(record)
    .map(([key, value]) => ({ key, value }))
    .sort((a, b) => b.value - a.value)
}

function BreakdownTable({ title, record }: { title: string; record: Record<string, number> }) {
  const rows = toRows(record)
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <EmptyState message="No spend recorded." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{title}</TableHead>
                <TableHead className="text-right">Spend</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.key}>
                  <TableCell className="font-medium">{row.key}</TableCell>
                  <TableCell className="text-right tabular-nums">{usd(row.value, 4)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}

export function CostView() {
  const cost = useCost()

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Cost"
        description="Measured spend (real cost_usd per call), not estimated. The operational data is the product."
      />
      <QueryState query={cost}>
        {(report) => {
          const byDay = Object.entries(report.by_day)
            .map(([day, value]) => ({ day, value }))
            .sort((a, b) => a.day.localeCompare(b.day))
          return (
            <div className="flex flex-col gap-4">
              <Card>
                <CardContent className="flex flex-wrap gap-8 pt-5">
                  <Stat label="Total spend" value={usd(report.total, 4)} />
                  <Stat label="Cost / opportunity" value={usd(report.cost_per_opportunity, 4)} />
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Spend by day</CardTitle>
                </CardHeader>
                <CardContent>
                  {byDay.length === 0 ? (
                    <EmptyState message="No spend recorded yet." />
                  ) : (
                    <ResponsiveContainer width="100%" height={220}>
                      <BarChart data={byDay} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                        <XAxis dataKey="day" stroke="var(--color-muted-foreground)" fontSize={12} />
                        <YAxis
                          stroke="var(--color-muted-foreground)"
                          fontSize={12}
                          tickFormatter={(value: number) => usd(value, 2)}
                        />
                        <Tooltip formatter={(value: number) => usd(value, 4)} />
                        <Bar dataKey="value" fill="var(--color-chart-1)" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  )}
                </CardContent>
              </Card>

              <div className="grid gap-4 lg:grid-cols-3">
                <BreakdownTable title="Agent" record={report.by_agent} />
                <BreakdownTable title="Model" record={report.by_model} />
                <BreakdownTable title="Category" record={report.by_category} />
              </div>
            </div>
          )
        }}
      </QueryState>
    </div>
  )
}
