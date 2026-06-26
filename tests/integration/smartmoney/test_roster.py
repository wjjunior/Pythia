"""Integration tests (PYT-91, AC-S4): roster transitions persist + audit; discovery seeds trials."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session

from pythia.common import TrackedTrader, TraderScorecard, TraderState
from pythia.db.smartmoney import RosterEventRepo, TrackedTraderRepo
from pythia.ingestion.falcon import FalconClient, FixtureFalconTransport
from pythia.smartmoney.roster import RosterManager, RosterPolicy, plan_transitions

pytestmark = pytest.mark.integration

_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "falcon"
_POLICY = RosterPolicy(
    max_traders=2,
    roster_max=3,
    promote_score=1.0,
    promote_agree=0.1,
    demote_score=0.0,
    demote_agree=0.0,
    drawdown_floor=0.5,
    bench_ttl=timedelta(days=14),
)


def test_apply_changes_state_and_audits_every_transition(session: Session) -> None:
    traders = TrackedTraderRepo(session)
    events = RosterEventRepo(session)
    trader = traders.add(
        TrackedTrader(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "t")),
            wallet="0xt",
            state=TraderState.TRIAL,
            added_at=_TS,
            state_since=_TS,
        )
    )
    card = TraderScorecard(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, "sc")),
        trader_id=trader.id,
        asof=_TS,
        n_resolved=30,
        market_class_ok=True,
        trader_score=2.0,
        agreed_bpd=0.2,
        max_drawdown=0.1,
    )

    transitions = plan_transitions([trader], {trader.id: card}, _POLICY, asof=_TS)
    RosterManager(traders, events).apply(transitions, at=_TS)

    moved = traders.get(trader.id)
    assert moved is not None and moved.state is TraderState.ACTIVE  # persisted
    history = events.list_for_trader(trader.id)
    assert len(history) == 1
    assert history[0].to_state is TraderState.ACTIVE and "promoted" in history[0].reason  # audited


def test_discover_seeds_leaderboard_wallets_as_trial(session: Session) -> None:
    traders = TrackedTraderRepo(session)
    events = RosterEventRepo(session)
    falcon = FalconClient(FixtureFalconTransport(_FIXTURES))
    manager = RosterManager(traders, events)

    added = manager.discover(falcon, at=_TS, limit=10, capacity=10)

    assert len(added) == 3  # the three fixture top-traders
    assert all(t.state is TraderState.TRIAL for t in added)
    assert all(events.list_for_trader(t.id) for t in added)  # each entry is audited
    # idempotent: re-discovery adds none
    assert manager.discover(falcon, at=_TS, limit=10, capacity=10) == []


def test_discover_seeds_at_most_the_remaining_capacity(session: Session) -> None:
    manager = RosterManager(TrackedTraderRepo(session), RosterEventRepo(session))
    falcon = FalconClient(FixtureFalconTransport(_FIXTURES))

    first = manager.discover(falcon, at=_TS, limit=10, capacity=2)
    assert len(first) == 2  # bounded to the remaining capacity, not the 3 available

    assert manager.discover(falcon, at=_TS, limit=10, capacity=0) == []  # full roster ⇒ none

    third = manager.discover(falcon, at=_TS, limit=10, capacity=5)
    assert len(third) == 1  # only the one still-untracked wallet remains
