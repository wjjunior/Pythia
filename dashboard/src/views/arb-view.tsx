import { useState } from "react"

import { EmptyState, QueryState } from "@/components/async-boundary"
import { MetricHeader } from "@/components/metric-header"
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
import { useApproveArb, useArbs, usePaper } from "@/hooks/use-api"
import { arbKindLabel, basketCost, capitalFit, legsByPrice, rankByEv, returnOnCost } from "@/lib/arb"
import { isoDateTime, num, pct, usd } from "@/lib/format"
import type { ArbItem } from "@/lib/types"

const EV_HINT = "Locked profit per unit basket. The basket pays $1 (one option wins); you pay Σ leg price. EV = 1 − Σ price."
const RETURN_HINT = "EV ÷ the basket cost — the return on the capital you deploy."

function ApproveControl({ arb, available }: { arb: ArbItem; available: number | undefined }) {
  const [baskets, setBaskets] = useState(10)
  const [confirming, setConfirming] = useState(false)
  const approve = useApproveArb()

  const cost = baskets * basketCost(arb)
  const locked = baskets * arb.ev
  const exceeds = available !== undefined && capitalFit(cost, available) === "exceeds"

  return (
    <div className="flex flex-wrap items-center gap-3 border-t pt-4">
      <label className="flex items-center gap-2 text-sm text-muted-foreground">
        Baskets
        <input
          type="number"
          min={1}
          value={baskets}
          onChange={(event) => setBaskets(Math.max(1, Number(event.target.value) || 1))}
          className="h-8 w-20 rounded-md border bg-transparent px-2 text-right tabular-nums text-foreground"
        />
      </label>
      <span className="text-sm tabular-nums">
        cost {usd(cost)} · locks <span className="text-positive">{usd(locked)}</span>
      </span>
      {exceeds && available !== undefined ? (
        <Badge variant="negative">exceeds available {usd(available)}</Badge>
      ) : null}
      {approve.isError ? (
        <span className="text-sm text-destructive">approve failed — it may already be open</span>
      ) : null}
      <div className="ml-auto flex gap-2">
        {confirming ? (
          <>
            <Button
              size="sm"
              disabled={approve.isPending}
              onClick={() =>
                approve.mutate({ id: arb.id, baskets }, { onSuccess: () => setConfirming(false) })
              }
            >
              {approve.isPending ? "Opening…" : `Confirm: open ${arb.legs.length} legs · ${usd(cost)}`}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setConfirming(false)}>
              cancel
            </Button>
          </>
        ) : (
          <Button size="sm" onClick={() => setConfirming(true)}>
            Approve
          </Button>
        )}
      </div>
    </div>
  )
}

function BasketCard({ arb, available }: { arb: ArbItem; available: number | undefined }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <CardTitle>{arb.event_slug}</CardTitle>
              <Badge variant="secondary">{arbKindLabel(arb.kind)}</Badge>
              {arb.approved ? <Badge variant="positive">✓ Approved</Badge> : null}
            </div>
            <p className="text-sm text-muted-foreground">
              Buy YES on all {arb.legs.length} options · cost {usd(basketCost(arb))}/basket
              {arb.first_detected_at ? ` · detected ${isoDateTime(arb.first_detected_at)}` : ""}
              {arb.last_seen_at ? ` · seen ${isoDateTime(arb.last_seen_at)}` : ""}
            </p>
            <p className="text-xs text-muted-foreground">
              ⚠ paper arb at mid prices — confirm you can fill every leg before sizing up
            </p>
          </div>
          <div className="flex gap-8">
            <Stat label={<MetricHeader label="EV / basket" hint={EV_HINT} />} value={num(arb.ev, 3)} />
            <Stat
              label={<MetricHeader label="Return on cost" hint={RETURN_HINT} />}
              value={pct(returnOnCost(arb))}
            />
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Market</TableHead>
              <TableHead>Side</TableHead>
              <TableHead className="text-right">Price</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {legsByPrice(arb.legs).map((leg) => (
              <TableRow key={`${leg.market_id}-${leg.side}`}>
                <TableCell className="font-medium">
                  {leg.question || leg.market_id.slice(0, 8)}
                </TableCell>
                <TableCell>
                  <Badge variant={leg.side === "YES" ? "positive" : "negative"}>{leg.side}</Badge>
                </TableCell>
                <TableCell className="text-right tabular-nums">{pct(leg.price)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {arb.approved ? null : <ApproveControl arb={arb} available={available} />}
      </CardContent>
    </Card>
  )
}

export function ArbView() {
  const arbs = useArbs()
  const paper = usePaper()
  const available = paper.data?.capital.available_capital

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Arbitrage"
        description="Structural arbitrage baskets — buy every option of an event for less than the $1 it pays. Approve to open all legs as paper positions; prices are mid, so confirm fillability first."
      />
      <QueryState query={arbs}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState message="No structural arbitrage detected." />
          ) : (
            <div className="flex flex-col gap-4">
              {rankByEv(items).map((arb) => (
                <BasketCard key={arb.id} arb={arb} available={available} />
              ))}
            </div>
          )
        }
      </QueryState>
    </div>
  )
}
