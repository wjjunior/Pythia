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
import { useArbs } from "@/hooks/use-api"
import { arbKindLabel, rankByEv, returnOnCost } from "@/lib/arb"
import { num, pct } from "@/lib/format"
import type { ArbItem } from "@/lib/types"

function BasketCard({ arb }: { arb: ArbItem }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <CardTitle>{arb.event_slug}</CardTitle>
            <Badge variant="secondary">{arbKindLabel(arb.kind)}</Badge>
          </div>
          <div className="flex gap-8">
            <Stat label="EV / basket" value={num(arb.ev, 3)} />
            <Stat label="Return on cost" value={pct(returnOnCost(arb))} />
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Market</TableHead>
              <TableHead>Side</TableHead>
              <TableHead className="text-right">Price</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {arb.legs.map((leg) => (
              <TableRow key={`${leg.market_id}-${leg.side}`}>
                <TableCell className="font-mono text-xs">{leg.market_id.slice(0, 8)}</TableCell>
                <TableCell>
                  <Badge variant={leg.side === "YES" ? "positive" : "negative"}>{leg.side}</Badge>
                </TableCell>
                <TableCell className="text-right tabular-nums">{num(leg.price, 3)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

export function ArbView() {
  const arbs = useArbs()

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Arbitrage"
        description="Structural arbitrage baskets — dutch books and ladder spreads — ranked by locked EV."
      />
      <QueryState query={arbs}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState message="No structural arbitrage detected." />
          ) : (
            <div className="flex flex-col gap-4">
              {rankByEv(items).map((arb) => (
                <BasketCard key={arb.id} arb={arb} />
              ))}
            </div>
          )
        }
      </QueryState>
    </div>
  )
}
