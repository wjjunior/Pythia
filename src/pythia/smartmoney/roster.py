"""Roster lifecycle (v2 §4.3, §3.6): discover → trial → active → benched → retired.

``plan_transitions`` is pure: given the roster and the latest scorecards it returns the audited
state changes (promote / demote / retire / evict) under the bounded-slot policy. ``RosterManager``
applies them — one ``RosterEvent`` per change with a human-readable reason — and ``discover`` seeds
new wallets from the Falcon leaderboard as ``trial``.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pythia.common import RosterEvent, TrackedTrader, TraderScorecard, TraderState
from pythia.common.ids import deterministic_id
from pythia.common.logging import get_logger
from pythia.db.smartmoney import RosterEventRepo, TrackedTraderRepo
from pythia.ingestion.falcon import FalconPort

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RosterPolicy:
    max_traders: int
    promote_score: float
    promote_agree: float
    demote_score: float
    demote_agree: float
    drawdown_floor: float


@dataclass(frozen=True, slots=True)
class RosterTransition:
    trader_id: str
    from_state: TraderState
    to_state: TraderState
    reason: str
    metrics: dict[str, Any]


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _snapshot(card: TraderScorecard) -> dict[str, Any]:
    return {
        "trader_score": _finite(card.trader_score),
        "agreed_bpd": _finite(card.agreed_bpd),
        "max_drawdown": _finite(card.max_drawdown),
        "n_resolved": card.n_resolved,
    }


def _agree(card: TraderScorecard) -> float:
    return card.agreed_bpd if card.agreed_bpd is not None else float("-inf")


_PROMOTABLE = (TraderState.TRIAL, TraderState.BENCHED)


def plan_transitions(
    traders: list[TrackedTrader],
    scorecards: dict[str, TraderScorecard],
    policy: RosterPolicy,
) -> list[RosterTransition]:
    """Pure: the audited promote / demote / retire / evict decisions for one review (§4.3)."""
    state: dict[str, TraderState] = {t.id: t.state for t in traders}
    transitions: list[RosterTransition] = []

    def move(trader_id: str, to: TraderState, reason: str, card: TraderScorecard) -> None:
        if state[trader_id] is to:
            return
        transitions.append(
            RosterTransition(trader_id, state[trader_id], to, reason, _snapshot(card))
        )
        state[trader_id] = to

    # 1. Hard retire on a drawdown-floor breach (any non-retired wallet).
    for trader in traders:
        card = scorecards.get(trader.id)
        if card is None or state[trader.id] is TraderState.RETIRED:
            continue
        if card.max_drawdown is not None and card.max_drawdown > policy.drawdown_floor:
            move(
                trader.id,
                TraderState.RETIRED,
                f"retired: max_drawdown {card.max_drawdown:.2f}",
                card,
            )

    # 2. Demote active wallets that drifted below the trailing thresholds.
    for trader in traders:
        card = scorecards.get(trader.id)
        if card is None or state[trader.id] is not TraderState.ACTIVE:
            continue
        if card.trader_score < policy.demote_score or _agree(card) < policy.demote_agree:
            move(trader.id, TraderState.BENCHED, f"benched: score {card.trader_score:.3f}", card)

    # 3. Promote qualifying trial/benched wallets, bounded by slots with eviction of the weakest.
    def qualifies(card: TraderScorecard | None) -> bool:
        # -inf hard floor fails ``>= promote_score``; a no-downside +inf scorer correctly passes it.
        return (
            card is not None
            and card.trader_score >= policy.promote_score
            and _agree(card) >= policy.promote_agree
        )

    candidates = sorted(
        (t for t in traders if state[t.id] in _PROMOTABLE and qualifies(scorecards.get(t.id))),
        key=lambda t: scorecards[t.id].trader_score,
        reverse=True,
    )
    for trader in candidates:
        card = scorecards[trader.id]
        active = [tid for tid, st in state.items() if st is TraderState.ACTIVE]
        if len(active) < policy.max_traders:
            move(trader.id, TraderState.ACTIVE, f"promoted: score {card.trader_score:.3f}", card)
            continue
        scored = [tid for tid in active if tid in scorecards]
        if not scored:
            continue
        weakest = min(scored, key=lambda tid: scorecards[tid].trader_score)
        if scorecards[weakest].trader_score < card.trader_score:  # only evict for a stronger trial
            move(weakest, TraderState.BENCHED, f"evicted by {trader.id[:8]}", scorecards[weakest])
            move(
                trader.id, TraderState.ACTIVE, f"promoted (evicting): {card.trader_score:.3f}", card
            )
    return transitions


class RosterManager:
    def __init__(self, trader_repo: TrackedTraderRepo, event_repo: RosterEventRepo) -> None:
        self._traders = trader_repo
        self._events = event_repo

    def apply(self, transitions: list[RosterTransition], *, at: datetime) -> None:
        for tr in transitions:
            self._traders.set_state(tr.trader_id, tr.to_state, at=at)
            self._events.add(
                RosterEvent(
                    id=deterministic_id("roster", tr.trader_id, tr.to_state.value, at.isoformat()),
                    trader_id=tr.trader_id,
                    to_state=tr.to_state,
                    from_state=tr.from_state,
                    reason=tr.reason,
                    metrics=tr.metrics,
                    created_at=at,
                )
            )
            _log.info(
                "roster_transition", trader_id=tr.trader_id, to=tr.to_state.value, reason=tr.reason
            )

    def discover(self, falcon: FalconPort, *, at: datetime, limit: int) -> list[TrackedTrader]:
        """Seed leaderboard wallets we don't already track as trial (§4.3)."""
        added: list[TrackedTrader] = []
        for top in falcon.list_top_traders(limit=limit):
            if self._traders.get_by_wallet(top.address) is not None:
                continue
            trader = self._traders.add(
                TrackedTrader(
                    id=deterministic_id("trader", top.address),
                    wallet=top.address,
                    state=TraderState.TRIAL,
                    added_at=at,
                    state_since=at,
                )
            )
            self._events.add(
                RosterEvent(
                    id=deterministic_id("roster", trader.id, "trial", at.isoformat()),
                    trader_id=trader.id,
                    to_state=TraderState.TRIAL,
                    reason=f"discovered: leaderboard rank {top.rank}",
                    metrics={"roi": _finite(top.roi), "total_pnl": _finite(top.total_pnl)},
                    created_at=at,
                )
            )
            added.append(trader)
        return added
