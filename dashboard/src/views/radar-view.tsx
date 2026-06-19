import { useNavigate } from "react-router-dom"

import { EmptyState, QueryState } from "@/components/async-boundary"
import { PageHeader } from "@/components/page"
import { Badge } from "@/components/ui/badge"
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
import { num, pct, signedNum } from "@/lib/format"
import { impliedSide, marketPrice, rankByOpportunity } from "@/lib/ranking"

export function RadarView() {
  const navigate = useNavigate()
  const radar = useRadar()

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Opportunity radar"
        description="Ranked by opportunity_score. The model estimates; deterministic math sizes and decides."
      />
      <QueryState query={radar}>
        {(items) => {
          const ranked = rankByOpportunity(items)
          if (ranked.length === 0) {
            return <EmptyState message="No scored opportunities yet." />
          }
          return (
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Market</TableHead>
                    <TableHead>Category</TableHead>
                    <TableHead>Side</TableHead>
                    <TableHead className="text-right">p_model</TableHead>
                    <TableHead className="text-right">market q</TableHead>
                    <TableHead className="text-right">net edge</TableHead>
                    <TableHead className="text-right">Kelly</TableHead>
                    <TableHead className="text-right">score</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {ranked.map((item) => {
                    const side = impliedSide(item.score)
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
                          <Badge variant={side === "YES" ? "positive" : "negative"}>{side}</Badge>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {pct(item.estimate.p_model)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">
                          {pct(marketPrice(item))}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {signedNum(item.score.edge_net)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {pct(item.score.kelly_fraction)}
                        </TableCell>
                        <TableCell className="text-right font-semibold tabular-nums">
                          {num(item.score.opportunity_score, 4)}
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
