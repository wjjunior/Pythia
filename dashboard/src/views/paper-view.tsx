import { ExternalLink } from "lucide-react"
import { useState } from "react"

import { EmptyState, QueryState } from "@/components/async-boundary"
import { PageHeader } from "@/components/page"
import { Stat } from "@/components/stat"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useClosePosition, usePaper, usePortfolio } from "@/hooks/use-api"
import { isoDate, pct, signedNum, usd } from "@/lib/format"
import { polymarketUrl } from "@/lib/polymarket"
import { exposureSeverity } from "@/lib/ranking"
import type { PaperPositionView } from "@/lib/types"

const SEVERITY_VARIANT = { ok: "positive", warn: "muted", high: "negative" } as const

function ExposureCard({
  title,
  label,
  record,
}: {
  title: string
  label: string
  record: Record<string, number>
}) {
  const rows = Object.entries(record).sort((a, b) => b[1] - a[1])
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <EmptyState message="No exposure." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{label}</TableHead>
                <TableHead className="text-right">Exposure</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map(([key, value]) => (
                <TableRow key={key}>
                  <TableCell className="font-medium">{key}</TableCell>
                  <TableCell className="text-right tabular-nums">{usd(value)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}

function SideBadge({ side }: { side: PaperPositionView["trade"]["side"] }) {
  return <Badge variant={side === "YES" ? "positive" : "negative"}>{side}</Badge>
}

function MarketCell({ view }: { view: PaperPositionView }) {
  const url = polymarketUrl(view.event_slug)
  return (
    <div className="flex max-w-[24rem] flex-col gap-1">
      <div className="flex items-center gap-2">
        <span className="truncate font-medium" title={view.question}>
          {view.question}
        </span>
        {view.market_closed ? <Badge variant="negative">stale</Badge> : null}
      </div>
      {url ? (
        <a
          href={url}
          target="_blank"
          rel="noreferrer noopener"
          className="inline-flex w-fit items-center gap-1 text-xs text-muted-foreground hover:text-foreground hover:underline"
        >
          Polymarket <ExternalLink className="h-3 w-3" />
        </a>
      ) : null}
    </div>
  )
}

function OpenPositionRow({ view }: { view: PaperPositionView }) {
  const close = useClosePosition()
  const [confirming, setConfirming] = useState(false)
  const { trade } = view
  return (
    <TableRow>
      <TableCell>
        <MarketCell view={view} />
      </TableCell>
      <TableCell className="tabular-nums text-muted-foreground">{isoDate(trade.opened_at)}</TableCell>
      <TableCell>
        <SideBadge side={trade.side} />
      </TableCell>
      <TableCell className="text-right tabular-nums">{pct(trade.entry_price)}</TableCell>
      <TableCell className="text-right tabular-nums">{usd(trade.size)}</TableCell>
      <TableCell className="text-right">
        <span className="inline-flex items-center justify-end gap-2">
          {close.isError ? <span className="text-xs text-destructive">failed</span> : null}
          {confirming ? (
            <>
              <Button
                size="sm"
                variant="destructive"
                disabled={close.isPending}
                onClick={() => close.mutate(trade.id, { onSuccess: () => setConfirming(false) })}
              >
                {close.isPending ? "Closing…" : "Confirm close"}
              </Button>
              <Button size="sm" variant="outline" onClick={() => setConfirming(false)}>
                cancel
              </Button>
            </>
          ) : (
            <Button size="sm" variant="outline" onClick={() => setConfirming(true)}>
              Close
            </Button>
          )}
        </span>
      </TableCell>
    </TableRow>
  )
}

const RESOLVED_STATUS_VARIANT = { resolved: "muted", void: "outline" } as const

function ResolvedRow({ view }: { view: PaperPositionView }) {
  const { trade } = view
  const variant = trade.status === "void" ? RESOLVED_STATUS_VARIANT.void : RESOLVED_STATUS_VARIANT.resolved
  return (
    <TableRow>
      <TableCell>
        <MarketCell view={view} />
      </TableCell>
      <TableCell className="tabular-nums text-muted-foreground">
        {trade.resolved_at ? isoDate(trade.resolved_at) : "—"}
      </TableCell>
      <TableCell>
        <SideBadge side={trade.side} />
      </TableCell>
      <TableCell className="text-right tabular-nums">{pct(trade.entry_price)}</TableCell>
      <TableCell className="text-right tabular-nums">{usd(trade.size)}</TableCell>
      <TableCell>
        <Badge variant={variant}>{trade.status}</Badge>
      </TableCell>
      <TableCell className="text-right tabular-nums">
        {trade.pnl == null ? "—" : signedNum(trade.pnl, 2)}
      </TableCell>
    </TableRow>
  )
}

export function PaperView() {
  const paper = usePaper()
  const portfolio = usePortfolio()

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Paper portfolio"
        description="Capital is recomputed from the ledger: available = bankroll + realized − locked."
      />
      <QueryState query={paper}>
        {({ capital, positions, resolved }) => {
          const severity = exposureSeverity(capital.exposure_fraction)
          return (
            <div className="flex flex-col gap-4">
              <Card>
                <CardContent className="flex flex-wrap gap-8 pt-5">
                  <Stat label="Available" value={usd(capital.available_capital)} />
                  <Stat label="Deployed" value={usd(capital.locked_in_open)} />
                  <Stat
                    label="Realized PnL"
                    value={
                      <span className={capital.realized_pnl >= 0 ? "text-positive" : "text-negative"}>
                        {usd(capital.realized_pnl)}
                      </span>
                    }
                  />
                  <Stat label="Starting bankroll" value={usd(capital.starting_bankroll)} />
                  <Stat
                    label="Exposure"
                    value={pct(capital.exposure_fraction)}
                    sub={<Badge variant={SEVERITY_VARIANT[severity]}>{severity}</Badge>}
                  />
                </CardContent>
              </Card>

              {portfolio.data ? (
                <div className="grid gap-4 lg:grid-cols-2">
                  <ExposureCard
                    title="Exposure by category"
                    label="Category"
                    record={portfolio.data.exposure.per_category}
                  />
                  <ExposureCard
                    title="Exposure by correlation group"
                    label="Group"
                    record={portfolio.data.exposure.per_correlation_group}
                  />
                </div>
              ) : null}

              <Card>
                <CardHeader>
                  <CardTitle>Open positions ({positions.length})</CardTitle>
                </CardHeader>
                <CardContent>
                  {positions.length === 0 ? (
                    <EmptyState message="No open paper positions." />
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Market</TableHead>
                          <TableHead>Opened</TableHead>
                          <TableHead>Side</TableHead>
                          <TableHead className="text-right">Entry</TableHead>
                          <TableHead className="text-right">Size</TableHead>
                          <TableHead className="text-right">Action</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {positions.map((view) => (
                          <OpenPositionRow key={view.trade.id} view={view} />
                        ))}
                      </TableBody>
                    </Table>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Resolved & closed ({resolved.length})</CardTitle>
                </CardHeader>
                <CardContent>
                  {resolved.length === 0 ? (
                    <EmptyState message="No settled positions yet." />
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Market</TableHead>
                          <TableHead>Settled</TableHead>
                          <TableHead>Side</TableHead>
                          <TableHead className="text-right">Entry</TableHead>
                          <TableHead className="text-right">Size</TableHead>
                          <TableHead>Status</TableHead>
                          <TableHead className="text-right">PnL</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {resolved.map((view) => (
                          <ResolvedRow key={view.trade.id} view={view} />
                        ))}
                      </TableBody>
                    </Table>
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
