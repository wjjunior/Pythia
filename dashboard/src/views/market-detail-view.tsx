import { ArrowLeft, ExternalLink } from "lucide-react"
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { Link, useParams } from "react-router-dom"

import { EmptyState, QueryState } from "@/components/async-boundary"
import { PageHeader } from "@/components/page"
import { Stat } from "@/components/stat"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useMarket } from "@/hooks/use-api"
import { isoDate, isoDateTime, pct } from "@/lib/format"
import type { Evidence, MarketSnapshot } from "@/lib/types"

function priceSeries(snapshots: MarketSnapshot[]) {
  return [...snapshots]
    .sort((a, b) => a.ts.localeCompare(b.ts))
    .map((snapshot) => ({ ts: isoDate(snapshot.ts), q: snapshot.yes_price }))
}

function byObservedDesc(a: Evidence, b: Evidence): number {
  return b.observed_at.localeCompare(a.observed_at)
}

export function MarketDetailView() {
  const { id = "" } = useParams()
  const market = useMarket(id)

  return (
    <div className="flex flex-col gap-4">
      <Link
        to="/"
        className="flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> Radar
      </Link>
      <QueryState query={market}>
        {(detail) => {
          const series = priceSeries(detail.snapshots)
          const evidence = [...detail.evidence].sort(byObservedDesc)
          return (
            <div className="flex flex-col gap-4">
              <PageHeader title={detail.market.question} description={detail.market.resolution_rule} />
              <div className="flex flex-wrap gap-2">
                <Badge variant="muted">{detail.market.category}</Badge>
                {detail.market.resolution_source ? (
                  <Badge variant="outline">{detail.market.resolution_source}</Badge>
                ) : null}
                <Badge variant={detail.market.resolved ? "secondary" : "outline"}>
                  {detail.market.resolved ? "resolved" : "open"}
                </Badge>
              </div>

              {detail.latest_estimate ? (
                <Card>
                  <CardHeader>
                    <CardTitle>Latest estimate</CardTitle>
                  </CardHeader>
                  <CardContent className="flex flex-col gap-4">
                    <div className="flex flex-wrap gap-8">
                      <Stat label="p_model" value={pct(detail.latest_estimate.p_model)} />
                      <Stat label="confidence" value={pct(detail.latest_estimate.confidence)} />
                      <Stat
                        label="interval"
                        value={`${pct(detail.latest_estimate.lower)} – ${pct(detail.latest_estimate.upper)}`}
                      />
                      <Stat label="model" value={detail.latest_estimate.model_name} />
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {detail.latest_estimate.rationale}
                    </p>
                    {detail.latest_estimate.critic_notes ? (
                      <p className="rounded-md bg-muted/50 p-3 text-sm">
                        <span className="font-medium">Critic:</span>{" "}
                        {detail.latest_estimate.critic_notes}
                      </p>
                    ) : null}
                  </CardContent>
                </Card>
              ) : null}

              {series.length > 0 ? (
                <Card>
                  <CardHeader>
                    <CardTitle>Market price (q)</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <ResponsiveContainer width="100%" height={200}>
                      <LineChart data={series} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                        <XAxis dataKey="ts" stroke="var(--color-muted-foreground)" fontSize={12} />
                        <YAxis
                          domain={[0, 1]}
                          stroke="var(--color-muted-foreground)"
                          fontSize={12}
                          tickFormatter={(value: number) => pct(value, 0)}
                        />
                        <Tooltip formatter={(value: number) => pct(value)} />
                        <Line
                          type="monotone"
                          dataKey="q"
                          stroke="var(--color-chart-1)"
                          dot={false}
                          strokeWidth={2}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  </CardContent>
                </Card>
              ) : null}

              <Card>
                <CardHeader>
                  <CardTitle>Evidence ({evidence.length})</CardTitle>
                </CardHeader>
                <CardContent>
                  {evidence.length === 0 ? (
                    <EmptyState message="No cited evidence for this market." />
                  ) : (
                    <ul className="flex flex-col divide-y">
                      {evidence.map((item) => (
                        <li key={item.id} className="flex flex-col gap-1 py-3 first:pt-0">
                          <div className="flex items-center gap-2 text-xs text-muted-foreground">
                            <span className="tabular-nums">{isoDateTime(item.observed_at)}</span>
                            {item.url ? (
                              <a
                                href={item.url}
                                target="_blank"
                                rel="noreferrer noopener"
                                className="flex items-center gap-1 hover:text-foreground"
                              >
                                source <ExternalLink className="h-3 w-3" />
                              </a>
                            ) : null}
                          </div>
                          <p className="text-sm">{item.content}</p>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
            </div>
          )
        }}
      </QueryState>
    </div>
  )
}
