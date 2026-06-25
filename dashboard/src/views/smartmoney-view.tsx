import { ArrowRight, Pin, VolumeX } from "lucide-react"
import { useNavigate } from "react-router-dom"

import { EmptyState, QueryState } from "@/components/async-boundary"
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
import { useOverride, useRoster, useRosterHistory, useSmartCandidates } from "@/hooks/use-api"
import { isoDateTime, num, pct } from "@/lib/format"
import { rankRoster, shortWallet, STATE_BADGE } from "@/lib/smartmoney"
import type { TraderState } from "@/lib/types"

const cell = (value: number | null, render: (n: number) => string): string =>
  value === null ? "—" : render(value)

export function SmartMoneyView() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Smart money"
        description="Tracked sharp wallets, the candidate signals their entries trigger, and the roster-rotation audit trail. Pin promotes a wallet to active; mute benches it."
      />
      <RosterPanel />
      <CandidatePanel />
      <HistoryPanel />
    </div>
  )
}

function RosterPanel() {
  const roster = useRoster()
  const override = useOverride()

  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold tracking-tight">Monitored roster</h2>
      <QueryState query={roster}>
        {(entries) => {
          if (entries.length === 0) {
            return <EmptyState message="No tracked traders yet." />
          }
          return (
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Wallet</TableHead>
                    <TableHead>State</TableHead>
                    <TableHead className="text-right">score</TableHead>
                    <TableHead className="text-right">n</TableHead>
                    <TableHead className="text-right">agree</TableHead>
                    <TableHead className="text-right">bpd</TableHead>
                    <TableHead className="text-right">sortino</TableHead>
                    <TableHead className="text-right">max dd</TableHead>
                    <TableHead>last entry</TableHead>
                    <TableHead className="text-right">
                      <span className="sr-only">Override</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rankRoster(entries).map((entry) => {
                    const card = entry.scorecard
                    const state: TraderState = entry.trader.state
                    return (
                      <TableRow key={entry.trader.id}>
                        <TableCell
                          className="font-mono text-xs"
                          title={entry.trader.wallet}
                        >
                          {shortWallet(entry.trader.wallet)}
                        </TableCell>
                        <TableCell>
                          <Badge variant={STATE_BADGE[state]}>{state}</Badge>
                        </TableCell>
                        <TableCell className="text-right font-semibold tabular-nums">
                          {cell(card?.trader_score ?? null, (n) => num(n, 3))}
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">
                          {card?.n_resolved ?? "—"}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {cell(card?.agree_rate ?? null, (n) => pct(n))}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {cell(card?.agreed_bpd ?? null, (n) => num(n, 4))}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {cell(card?.sortino ?? null, (n) => num(n, 2))}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {cell(card?.max_drawdown ?? null, (n) => pct(n))}
                        </TableCell>
                        <TableCell className="max-w-xs truncate text-muted-foreground">
                          {entry.last_entry
                            ? `${entry.last_entry.side} · ${entry.last_entry.external_market}`
                            : "—"}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={state === "active" || override.isPending}
                              onClick={() =>
                                override.mutate({ traderId: entry.trader.id, action: "pin" })
                              }
                            >
                              <Pin className="h-3.5 w-3.5" />
                              Pin
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={state === "benched" || override.isPending}
                              onClick={() =>
                                override.mutate({ traderId: entry.trader.id, action: "mute" })
                              }
                            >
                              <VolumeX className="h-3.5 w-3.5" />
                              Mute
                            </Button>
                          </div>
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
    </section>
  )
}

function CandidatePanel() {
  const candidates = useSmartCandidates()
  const navigate = useNavigate()

  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold tracking-tight">Candidate feed</h2>
      <QueryState query={candidates}>
        {(items) => {
          if (items.length === 0) {
            return <EmptyState message="No candidate signals yet." />
          }
          return (
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Market</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead className="text-right">triggers</TableHead>
                    <TableHead>verdict</TableHead>
                    <TableHead>side</TableHead>
                    <TableHead className="text-right">score</TableHead>
                    <TableHead className="text-right">seen</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => {
                    const market = item.market
                    return (
                      <TableRow
                        key={item.candidate.id}
                        className={market ? "cursor-pointer" : undefined}
                        onClick={market ? () => navigate(`/markets/${market.id}`) : undefined}
                      >
                        <TableCell className="max-w-sm truncate font-medium">
                          {market?.question ?? item.candidate.market_id ?? "unresolved"}
                        </TableCell>
                        <TableCell>
                          <Badge variant="muted">{item.candidate.source}</Badge>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {item.candidate.trader_ids.length}
                        </TableCell>
                        <TableCell>
                          <Badge variant={item.viable ? "positive" : "muted"}>
                            {item.viable ? "viable" : "no edge"}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          {item.recommended_side ? (
                            <Badge
                              variant={
                                item.recommended_side === "YES" ? "positive" : "negative"
                              }
                            >
                              {item.recommended_side}
                            </Badge>
                          ) : (
                            "—"
                          )}
                        </TableCell>
                        <TableCell className="text-right font-semibold tabular-nums">
                          {cell(item.opportunity_score, (n) => num(n, 4))}
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">
                          {isoDateTime(item.candidate.created_at)}
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
    </section>
  )
}

function HistoryPanel() {
  const history = useRosterHistory()

  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold tracking-tight">Roster history</h2>
      <QueryState query={history}>
        {(items) => {
          if (items.length === 0) {
            return <EmptyState message="No roster transitions yet." />
          }
          return (
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>When</TableHead>
                    <TableHead>Wallet</TableHead>
                    <TableHead>Transition</TableHead>
                    <TableHead>Reason</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map(({ event, wallet }) => (
                    <TableRow key={event.id}>
                      <TableCell className="tabular-nums text-muted-foreground">
                        {isoDateTime(event.created_at)}
                      </TableCell>
                      <TableCell className="font-mono text-xs" title={wallet}>
                        {shortWallet(wallet)}
                      </TableCell>
                      <TableCell>
                        <span className="inline-flex items-center gap-1.5">
                          {event.from_state ? (
                            <Badge variant={STATE_BADGE[event.from_state]}>
                              {event.from_state}
                            </Badge>
                          ) : (
                            <Badge variant="outline">new</Badge>
                          )}
                          <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
                          <Badge variant={STATE_BADGE[event.to_state]}>{event.to_state}</Badge>
                        </span>
                      </TableCell>
                      <TableCell className="text-muted-foreground">{event.reason}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          )
        }}
      </QueryState>
    </section>
  )
}
