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
import { useRadar, useReview } from "@/hooks/use-api"
import { daysToClose, multiple, num, pct, signedNum } from "@/lib/format"
import { polymarketUrl } from "@/lib/polymarket"
import {
  type RadarColumn,
  DEFAULT_RADAR_FILTERS,
  discardedCount,
  isApprovable,
  RADAR_COLUMNS,
  type RadarFilters,
  type RadarSortKey,
  reviewStatusBadge,
  sortRadar,
  type SortDir,
  visibleRadar,
} from "@/lib/radar"
import type { RadarItem } from "@/lib/types"
import { cn } from "@/lib/utils"

const ACTION_HINT =
  "Approve into paper at the risk-approved size, discard the opportunity, or open it on Polymarket."
const NOT_APPROVABLE_HINT = "No risk-approved size — vetoed or not yet risk-evaluated."

interface SortState {
  key: RadarSortKey
  dir: SortDir
}

function defaultDir(key: RadarSortKey): SortDir {
  return RADAR_COLUMNS.find((column) => column.key === key)?.numeric ? "desc" : "asc"
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

function SortableHeader({
  column,
  sort,
  onSort,
}: {
  column: RadarColumn
  sort: SortState | null
  onSort: (key: RadarSortKey) => void
}) {
  const arrow = sort && sort.key === column.key ? (sort.dir === "asc" ? "▲" : "▼") : ""
  return (
    <TableHead className={column.numeric ? "text-right" : undefined}>
      <button
        type="button"
        onClick={() => onSort(column.key)}
        className={cn(
          "flex w-full select-none items-center gap-1 hover:text-foreground",
          column.numeric ? "justify-end" : "justify-start",
        )}
      >
        <MetricHeader label={column.label} hint={column.hint} />
        <span className="w-2 text-xs text-muted-foreground">{arrow}</span>
      </button>
    </TableHead>
  )
}

function RowActions({ item }: { item: RadarItem }) {
  const review = useReview(item.estimate.id)
  const [confirming, setConfirming] = useState<"approve" | "reject" | null>(null)
  const venueUrl = polymarketUrl(item.market.event_slug)

  const done = { onSuccess: () => setConfirming(null) }

  return (
    <span
      className="inline-flex items-center justify-end gap-2"
      onClick={(event) => event.stopPropagation()}
    >
      {review.isError ? <span className="text-xs text-destructive">failed</span> : null}
      {confirming === "approve" ? (
        <>
          <Button
            size="sm"
            disabled={review.isPending}
            onClick={() =>
              review.mutate(
                { action: "approve", side: item.recommended_side, size: item.suggested_size },
                done,
              )
            }
          >
            {review.isPending
              ? "Opening…"
              : `Confirm: ${num(item.suggested_size, 2)} sh ${item.recommended_side}`}
          </Button>
          <Button size="sm" variant="outline" onClick={() => setConfirming(null)}>
            cancel
          </Button>
        </>
      ) : confirming === "reject" ? (
        <>
          <Button
            size="sm"
            variant="destructive"
            disabled={review.isPending}
            onClick={() => review.mutate({ action: "reject" }, done)}
          >
            {review.isPending ? "Discarding…" : "Confirm discard"}
          </Button>
          <Button size="sm" variant="outline" onClick={() => setConfirming(null)}>
            cancel
          </Button>
        </>
      ) : (
        <>
          <Button
            size="sm"
            disabled={!isApprovable(item)}
            title={isApprovable(item) ? undefined : NOT_APPROVABLE_HINT}
            onClick={() => setConfirming("approve")}
          >
            Approve
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={item.review_status !== "none"}
            onClick={() => setConfirming("reject")}
          >
            Discard
          </Button>
        </>
      )}
      {venueUrl ? (
        <a
          href={venueUrl}
          target="_blank"
          rel="noreferrer noopener"
          className="text-muted-foreground hover:text-foreground"
          aria-label="Open on Polymarket"
          title="Open on Polymarket"
        >
          <ExternalLink className="h-4 w-4" />
        </a>
      ) : null}
    </span>
  )
}

function RadarRow({ item }: { item: RadarItem }) {
  const navigate = useNavigate()
  const badge = reviewStatusBadge(item.review_status)
  return (
    <TableRow className="cursor-pointer" onClick={() => navigate(`/markets/${item.market.id}`)}>
      <TableCell className="max-w-sm truncate font-medium">{item.market.question}</TableCell>
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
      <TableCell className="text-right tabular-nums">{pct(item.estimate.p_model)}</TableCell>
      <TableCell className="text-right tabular-nums text-muted-foreground">
        {pct(item.entry_price)}
      </TableCell>
      <TableCell className="text-right font-medium tabular-nums">
        {multiple(item.win_multiple)}
      </TableCell>
      <TableCell className="text-right tabular-nums">{pct(item.win_probability)}</TableCell>
      <TableCell className="text-right tabular-nums">{signedNum(item.score.edge_net)}</TableCell>
      <TableCell className="text-right font-semibold tabular-nums">
        {num(item.score.opportunity_score, 4)}
      </TableCell>
      <TableCell>
        {badge ? <Badge variant={badge.variant}>{badge.label}</Badge> : null}
      </TableCell>
      <TableCell className="text-right">
        <RowActions item={item} />
      </TableCell>
    </TableRow>
  )
}

export function RadarView() {
  const [filters, setFilters] = useState<RadarFilters>(DEFAULT_RADAR_FILTERS)
  const [sort, setSort] = useState<SortState | null>(null)
  const [showDiscarded, setShowDiscarded] = useState(false)
  const radar = useRadar(filters)

  const onSort = (key: RadarSortKey) =>
    setSort((prev) =>
      prev && prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key, dir: defaultDir(key) },
    )

  const discarded = discardedCount(radar.data ?? [])

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Opportunity radar"
        description="The calibration queue, ranked by opportunity score. Click a row to review it beside the cited evidence, or approve it straight into paper. Hover a header for what it means; click a header to sort the loaded rows."
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
        <label className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={showDiscarded}
            disabled={discarded === 0}
            onChange={(event) => setShowDiscarded(event.target.checked)}
          />
          Show discarded ({discarded})
        </label>
      </div>

      <QueryState query={radar}>
        {(items) => {
          // No explicit sort ⇒ keep the server's ranking (score desc, or soonest-resolution when
          // windowed); a header click switches to a client-side sort of the loaded rows.
          const visible = visibleRadar(items, showDiscarded)
          const rows = sort ? sortRadar(visible, sort.key, sort.dir) : visible
          if (rows.length === 0) {
            return <EmptyState message="No opportunities match these filters." />
          }
          return (
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    {RADAR_COLUMNS.map((column) => (
                      <SortableHeader
                        key={column.key}
                        column={column}
                        sort={sort}
                        onSort={onSort}
                      />
                    ))}
                    <TableHead className="text-right">
                      <MetricHeader label="action" hint={ACTION_HINT} />
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((item) => (
                    <RadarRow key={item.market.id} item={item} />
                  ))}
                </TableBody>
              </Table>
            </Card>
          )
        }}
      </QueryState>
    </div>
  )
}
