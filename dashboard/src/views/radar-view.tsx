import { ExternalLink } from "lucide-react"
import { useState } from "react"
import { useNavigate } from "react-router-dom"

import { EmptyState, QueryState } from "@/components/async-boundary"
import { MetricHeader } from "@/components/metric-header"
import { PageHeader } from "@/components/page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useRadar } from "@/hooks/use-api"
import { daysToClose, multiple, num, pct, signedNum } from "@/lib/format"
import { polymarketUrl } from "@/lib/polymarket"
import { DEFAULT_RADAR_FILTERS, type RadarFilters, reviewStatusBadge } from "@/lib/radar"

const HINTS = {
  p_model: "Model probability for the recommended side.",
  entry: "Price you'd pay to enter the recommended side (its spread-adjusted ask).",
  win: "Payout multiple if the position wins ($1 ÷ entry price).",
  win_prob: "Model probability that the position wins.",
  net_edge: "Model edge after costs — p_win − entry, net of fees and slippage.",
  score: "Opportunity score — the radar's ranking metric (edge weighted by the confidence factors).",
}

function FilterField({
  label,
  value,
  step,
  onChange,
}: {
  label: string
  value: number
  step: number
  onChange: (value: number) => void
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
      {label}
      <input
        type="number"
        min={0}
        step={step}
        value={value}
        onChange={(event) => onChange(Math.max(0, Number(event.target.value) || 0))}
        className="h-8 w-28 rounded-md border bg-transparent px-2 text-right tabular-nums text-foreground"
      />
    </label>
  )
}

export function RadarView() {
  const navigate = useNavigate()
  const [filters, setFilters] = useState<RadarFilters>(DEFAULT_RADAR_FILTERS)
  const radar = useRadar(filters)

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Opportunity radar"
        description="The calibration queue, ranked by opportunity score. Click a row to review it — that opens the paper-trade gate beside the cited evidence. Hover a column header for what it means."
      />

      <div className="flex flex-wrap items-end gap-4 rounded-xl border bg-card p-4">
        <FilterField
          label="Resolve ≤ (days)"
          value={filters.maxDaysToResolve}
          step={1}
          onChange={(value) => setFilters({ ...filters, maxDaysToResolve: value })}
        />
        <FilterField
          label="Win prob ≥ (%)"
          value={Math.round(filters.minWinProbability * 100)}
          step={5}
          onChange={(value) => setFilters({ ...filters, minWinProbability: value / 100 })}
        />
        <FilterField
          label="Win multiple ≥ (×)"
          value={filters.minWinMultiple}
          step={0.1}
          onChange={(value) => setFilters({ ...filters, minWinMultiple: value })}
        />
        <Button variant="outline" size="sm" onClick={() => setFilters(DEFAULT_RADAR_FILTERS)}>
          Reset
        </Button>
      </div>

      <QueryState query={radar}>
        {(items) => {
          if (items.length === 0) {
            return <EmptyState message="No opportunities match these filters." />
          }
          return (
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Market</TableHead>
                    <TableHead>Category</TableHead>
                    <TableHead>Side</TableHead>
                    <TableHead className="text-right">resolves in</TableHead>
                    <TableHead className="text-right">closes in</TableHead>
                    <TableHead className="text-right">
                      <MetricHeader label="p_model" hint={HINTS.p_model} />
                    </TableHead>
                    <TableHead className="text-right">
                      <MetricHeader label="entry" hint={HINTS.entry} />
                    </TableHead>
                    <TableHead className="text-right">
                      <MetricHeader label="win" hint={HINTS.win} />
                    </TableHead>
                    <TableHead className="text-right">
                      <MetricHeader label="win prob" hint={HINTS.win_prob} />
                    </TableHead>
                    <TableHead className="text-right">
                      <MetricHeader label="net edge" hint={HINTS.net_edge} />
                    </TableHead>
                    <TableHead className="text-right">
                      <MetricHeader label="score" hint={HINTS.score} />
                    </TableHead>
                    <TableHead>status</TableHead>
                    <TableHead className="text-right">action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => {
                    const venueUrl = polymarketUrl(item.market.event_slug)
                    const badge = reviewStatusBadge(item.review_status)
                    return (
                      <TableRow
                        key={item.market.id}
                        className="cursor-pointer"
                        onClick={() => navigate(`/markets/${item.market.id}`)}
                      >
                        <TableCell className="max-w-sm truncate font-medium">
                          {item.market.question}
                        </TableCell>
                        <TableCell>
                          <Badge variant="muted">{item.market.category}</Badge>
                        </TableCell>
                        <TableCell>
                          <Badge variant={item.recommended_side === "YES" ? "positive" : "negative"}>
                            {item.recommended_side}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right font-medium tabular-nums">
                          {daysToClose(item.resolves_in_days)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">
                          {daysToClose(item.closes_in_days)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {pct(item.estimate.p_model)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">
                          {pct(item.entry_price)}
                        </TableCell>
                        <TableCell className="text-right font-medium tabular-nums">
                          {multiple(item.win_multiple)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {pct(item.win_probability)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {signedNum(item.score.edge_net)}
                        </TableCell>
                        <TableCell className="text-right font-semibold tabular-nums">
                          {num(item.score.opportunity_score, 4)}
                        </TableCell>
                        <TableCell>
                          {badge ? <Badge variant={badge.variant}>{badge.label}</Badge> : null}
                        </TableCell>
                        <TableCell className="text-right">
                          <span className="inline-flex items-center gap-2">
                            <span className="text-xs font-medium text-foreground">Review →</span>
                            {venueUrl ? (
                              <a
                                href={venueUrl}
                                target="_blank"
                                rel="noreferrer noopener"
                                onClick={(event) => event.stopPropagation()}
                                className="text-muted-foreground hover:text-foreground"
                                aria-label="Open on Polymarket"
                                title="Open on Polymarket"
                              >
                                <ExternalLink className="h-4 w-4" />
                              </a>
                            ) : null}
                          </span>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </Card>
          )
        }}
      </QueryState>
    </div>
  )
}
