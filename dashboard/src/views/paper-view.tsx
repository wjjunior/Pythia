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
import { usePaper } from "@/hooks/use-api"
import { isoDate, pct, signedNum, usd } from "@/lib/format"
import { exposureSeverity } from "@/lib/ranking"

const SEVERITY_VARIANT = { ok: "positive", warn: "muted", high: "negative" } as const

export function PaperView() {
  const paper = usePaper()

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Paper portfolio"
        description="Capital is recomputed from the ledger: available = bankroll + realized − locked."
      />
      <QueryState query={paper}>
        {({ capital, positions }) => {
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
                          <TableHead>Opened</TableHead>
                          <TableHead>Side</TableHead>
                          <TableHead className="text-right">Entry</TableHead>
                          <TableHead className="text-right">Size</TableHead>
                          <TableHead>Status</TableHead>
                          <TableHead className="text-right">PnL</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {positions.map((trade) => (
                          <TableRow key={trade.id}>
                            <TableCell className="tabular-nums text-muted-foreground">
                              {isoDate(trade.opened_at)}
                            </TableCell>
                            <TableCell>
                              <Badge variant={trade.side === "YES" ? "positive" : "negative"}>
                                {trade.side}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {pct(trade.entry_price)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {usd(trade.size)}
                            </TableCell>
                            <TableCell>
                              <Badge variant="muted">{trade.status}</Badge>
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {trade.pnl == null ? "—" : signedNum(trade.pnl, 2)}
                            </TableCell>
                          </TableRow>
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
