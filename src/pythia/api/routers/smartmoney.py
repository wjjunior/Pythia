"""Smart-money dashboard endpoints (§6): roster, candidate feed, history, audited override."""

import math
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from pythia.api.deps import SessionDep
from pythia.api.schemas import (
    CandidateFeedItem,
    OverrideAction,
    OverrideRequest,
    RosterEntry,
    RosterEventItem,
    ScorecardView,
)
from pythia.common import RosterEvent, TraderScorecard, TraderState
from pythia.common.ids import deterministic_id
from pythia.db.repositories import EstimateRepository, MarketRepository, ScoreRepository
from pythia.db.smartmoney import (
    CandidateSignalRepo,
    RosterEventRepo,
    TrackedTraderRepo,
    TraderEntryRepo,
    TraderScorecardRepo,
)
from pythia.scoring import recommended_side

router = APIRouter(tags=["smartmoney"])

_MAX_FEED = 200
_OVERRIDE_STATE = {OverrideAction.PIN: TraderState.ACTIVE, OverrideAction.MUTE: TraderState.BENCHED}


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _scorecard_view(card: TraderScorecard) -> ScorecardView:
    return ScorecardView(
        asof=card.asof,
        n_resolved=card.n_resolved,
        market_class_ok=card.market_class_ok,
        trader_score=_finite(card.trader_score),
        roi=_finite(card.roi),
        risk_adj_edge=_finite(card.risk_adj_edge),
        sortino=_finite(card.sortino),
        skew=_finite(card.skew),
        max_drawdown=_finite(card.max_drawdown),
        agree_rate=_finite(card.agree_rate),
        agreed_bpd=_finite(card.agreed_bpd),
    )


@router.get("/api/smartmoney/roster")
def roster(session: SessionDep) -> list[RosterEntry]:
    traders = TrackedTraderRepo(session)
    scorecards = TraderScorecardRepo(session)
    entries = TraderEntryRepo(session)
    events = RosterEventRepo(session)
    now = datetime.now(UTC)
    out: list[RosterEntry] = []
    for trader in traders.list():
        card = scorecards.latest(trader.id, now)
        last_event = events.latest_for_trader(trader.id)
        out.append(
            RosterEntry(
                trader=trader,
                scorecard=_scorecard_view(card) if card is not None else None,
                last_entry=entries.last_for_trader(trader.id, asof=now),
                reason=last_event.reason if last_event is not None else None,
            )
        )
    return out


@router.get("/api/smartmoney/candidates")
def candidates(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=_MAX_FEED)] = 50
) -> list[CandidateFeedItem]:
    markets = MarketRepository(session)
    estimates = EstimateRepository(session)
    scores = ScoreRepository(session)
    out: list[CandidateFeedItem] = []
    for candidate in CandidateSignalRepo(session).recent(limit):
        market = markets.get(candidate.market_id) if candidate.market_id else None
        score = None
        side = None
        if candidate.market_id is not None:
            estimate = estimates.latest_for_market(candidate.market_id)
            if estimate is not None:
                persisted = scores.get_by_estimate(estimate.id)
                if persisted is not None:
                    score = persisted
                    side = recommended_side(estimate.p_model, estimate.p_model - persisted.edge)
        out.append(
            CandidateFeedItem(
                candidate=candidate,
                market=market,
                viable=score is not None and score.opportunity_score > 0.0,
                opportunity_score=score.opportunity_score if score is not None else None,
                recommended_side=side,
            )
        )
    return out


@router.get("/api/smartmoney/roster-events")
def roster_events(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=_MAX_FEED)] = 50
) -> list[RosterEventItem]:
    traders = TrackedTraderRepo(session)
    wallets = {t.id: t.wallet for t in traders.list()}
    return [
        RosterEventItem(event=event, wallet=wallets.get(event.trader_id, "unknown"))
        for event in RosterEventRepo(session).recent(limit)
    ]


@router.post("/api/smartmoney/traders/{trader_id}/override")
def override(trader_id: uuid.UUID, body: OverrideRequest, session: SessionDep) -> RosterEntry:
    traders = TrackedTraderRepo(session)
    tid = str(trader_id)
    trader = traders.get(tid)
    if trader is None:
        raise HTTPException(status_code=404, detail="unknown trader")
    now = datetime.now(UTC)
    to_state = _OVERRIDE_STATE[body.action]
    RosterEventRepo(session).add(
        RosterEvent(
            id=deterministic_id("roster", tid, to_state.value, now.isoformat()),
            trader_id=tid,
            from_state=trader.state,
            to_state=to_state,
            reason="manual",  # never silently overrides the engine (§6)
            metrics={"action": body.action.value},
            created_at=now,
        )
    )
    moved = traders.set_state(tid, to_state, at=now)
    card = TraderScorecardRepo(session).latest(tid, now)
    return RosterEntry(
        trader=moved,
        scorecard=_scorecard_view(card) if card is not None else None,
        last_entry=TraderEntryRepo(session).last_for_trader(tid, asof=now),
        reason="manual",
    )
