"""Integration tests for the v2 smart-money repos (PYT-89): §4.6 invariants on real Postgres."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlmodel import Session, col

from pythia.common import (
    CandidateSignal,
    CandidateSignalStatus,
    CandidateSource,
    Evidence,
    Market,
    RosterEvent,
    TrackedTrader,
    TraderEntry,
    TraderScorecard,
    TraderState,
)
from pythia.db import models
from pythia.db.repositories import MarketRepository
from pythia.db.smartmoney import (
    CandidateSignalRepo,
    RosterEventRepo,
    TrackedTraderRepo,
    TraderEntryRepo,
    TraderScorecardRepo,
)
from pythia.orchestration.runner import _RepoTriggeredMarkets
from pythia.smartmoney.evidence import SMART_MONEY_SOURCE_ID, SmartMoneyEvidencePort

pytestmark = pytest.mark.integration

_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def _seed_trader(
    session: Session, *, wallet: str = "0xsharp", state: TraderState = TraderState.TRIAL
) -> str:
    return (
        TrackedTraderRepo(session)
        .add(
            TrackedTrader(
                id=_uid(wallet), wallet=wallet, state=state, added_at=_TS, state_since=_TS
            )
        )
        .id
    )


def _seed_market(session: Session, name: str) -> str:
    return (
        MarketRepository(session)
        .upsert(
            Market(
                id=_uid(name),
                external_id=name,
                question="q",
                category="politics",
                resolution_rule="r",
            )
        )
        .id
    )


def _entry_on(
    session: Session,
    *,
    trader_id: str,
    market_id: str,
    eid: str,
    ts: datetime,
    is_increase: bool = True,
    side: str = "YES",
) -> None:
    TraderEntryRepo(session).add(
        TraderEntry(
            id=_uid(eid),
            trader_id=trader_id,
            market_id=market_id,
            external_market="0xext",
            side=side,
            ts=ts,
            is_increase=is_increase,
        )
    )


def test_tracked_trader_crud_and_state_transition(session: Session) -> None:
    repo = TrackedTraderRepo(session)
    tid = _seed_trader(session, wallet="0xa")
    assert repo.get_by_wallet("0xa") is not None
    assert [t.id for t in repo.list(state=TraderState.TRIAL)] == [tid]

    moved = repo.set_state(tid, TraderState.ACTIVE, at=_TS + timedelta(days=1))
    assert moved.state is TraderState.ACTIVE
    assert moved.state_since == _TS + timedelta(days=1)
    assert repo.list(state=TraderState.TRIAL) == []
    assert [t.id for t in repo.list(state=TraderState.ACTIVE)] == [tid]


def test_invalid_trader_state_is_rejected(session: Session) -> None:
    bad = models.TrackedTrader(
        id=uuid.uuid5(uuid.NAMESPACE_URL, "bad"), wallet="0xbad", state="bogus"
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_scorecard_latest_is_point_in_time(session: Session) -> None:
    tid = _seed_trader(session)
    repo = TraderScorecardRepo(session)
    for n, asof in [(10, _TS), (20, _TS + timedelta(days=2))]:
        repo.add(
            TraderScorecard(
                id=_uid(f"sc{n}"),
                trader_id=tid,
                asof=asof,
                n_resolved=n,
                market_class_ok=True,
                trader_score=float(n),
            )
        )
    assert repo.latest(tid, _TS - timedelta(days=1)) is None  # before any scorecard
    near = repo.latest(tid, _TS + timedelta(days=1))
    assert near is not None and near.n_resolved == 10  # the t1 card, never the future t2
    far = repo.latest(tid, _TS + timedelta(days=3))
    assert far is not None and far.n_resolved == 20


def test_scorecard_is_append_only(session: Session) -> None:
    tid = _seed_trader(session)
    card = TraderScorecardRepo(session).add(
        TraderScorecard(
            id=_uid("sc"),
            trader_id=tid,
            asof=_TS,
            n_resolved=5,
            market_class_ok=True,
            trader_score=1.0,
        )
    )
    with pytest.raises(DBAPIError):
        session.execute(
            update(models.TraderScorecard)
            .where(col(models.TraderScorecard.id) == uuid.UUID(card.id))
            .values(trader_score=99.0)
        )
    session.rollback()


def test_entry_insert_is_idempotent_and_point_in_time(session: Session) -> None:
    tid = _seed_trader(session)
    repo = TraderEntryRepo(session)
    entry = TraderEntry(
        id=_uid("e1"), trader_id=tid, external_market="0xmkt", side="YES", ts=_TS, is_increase=True
    )
    repo.add(entry)
    repo.add(entry)  # re-poll → no duplicate (AC-S1)
    repo.add(
        TraderEntry(
            id=_uid("e2"),
            trader_id=tid,
            external_market="0xmkt",
            side="YES",
            ts=_TS + timedelta(days=2),
            is_increase=True,
        )
    )
    got = repo.list_for_trader(tid, asof=_TS + timedelta(days=1))
    assert [e.id for e in got] == [_uid("e1")]  # idempotent → one row; the future entry excluded


def test_entry_unique_constraint_rejects_a_raw_duplicate(session: Session) -> None:
    tid = _seed_trader(session)
    key = {
        "trader_id": uuid.UUID(tid),
        "external_market": "0xmkt",
        "side": "YES",
        "ts": _TS,
        "is_increase": True,
    }
    session.add(models.TraderEntry(id=uuid.uuid5(uuid.NAMESPACE_URL, "ed1"), **key))
    session.commit()
    session.add(models.TraderEntry(id=uuid.uuid5(uuid.NAMESPACE_URL, "ed2"), **key))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_candidate_signal_lifecycle(session: Session) -> None:
    tid = _seed_trader(session)
    repo = CandidateSignalRepo(session)
    sig = repo.enqueue(
        CandidateSignal(
            id=_uid("cs"),
            asof=_TS,
            trader_ids=[tid],
            source=CandidateSource.NEW_ENTRY,
            status=CandidateSignalStatus.QUEUED,
            created_at=_TS,
        )
    )
    assert [s.id for s in repo.list_by_status(CandidateSignalStatus.QUEUED)] == [sig.id]
    repo.set_status(sig.id, CandidateSignalStatus.EVALUATED)
    assert repo.list_by_status(CandidateSignalStatus.QUEUED) == []
    assert [s.id for s in repo.list_by_status(CandidateSignalStatus.EVALUATED)] == [sig.id]


def test_list_active_on_market_filters_active_pit_and_increases(session: Session) -> None:
    market = _seed_market(session, "mkt")
    other = _seed_market(session, "mkt-other")
    active = _seed_trader(session, wallet="0xactive", state=TraderState.ACTIVE)
    trial = _seed_trader(session, wallet="0xtrial", state=TraderState.TRIAL)

    _entry_on(session, trader_id=active, market_id=market, eid="ea1", ts=_TS)  # the only hit
    _entry_on(
        session,
        trader_id=active,
        market_id=market,
        eid="ea2",
        ts=_TS + timedelta(hours=1),
        is_increase=False,  # exit — excluded
    )
    _entry_on(
        session, trader_id=active, market_id=market, eid="ea3", ts=_TS + timedelta(days=3)
    )  # future — excluded by asof
    _entry_on(session, trader_id=active, market_id=other, eid="ea4", ts=_TS)  # other market
    _entry_on(session, trader_id=trial, market_id=market, eid="et1", ts=_TS)  # not active

    got = TraderEntryRepo(session).list_active_on_market(market, asof=_TS + timedelta(days=1))
    assert [e.id for e in got] == [_uid("ea1")]


def test_has_market_trigger_detects_attribution(session: Session) -> None:
    tid = _seed_trader(session)
    market = _seed_market(session, "mkt-trig")
    other = _seed_market(session, "mkt-untrig")
    repo = CandidateSignalRepo(session)
    assert repo.has_market_trigger(market) is False

    repo.enqueue(
        CandidateSignal(
            id=_uid("cs-trig"),
            market_id=market,
            asof=_TS,
            trader_ids=[tid],
            source=CandidateSource.NEW_ENTRY,
            status=CandidateSignalStatus.EVALUATED,
            created_at=_TS,
        )
    )
    assert repo.has_market_trigger(market) is True
    assert repo.has_market_trigger(other) is False


def test_has_market_trigger_sees_an_over_cap_queued_trigger(session: Session) -> None:
    # PYT-117/R-8: an over-cap QUEUED trigger now carries its market_id, so the guard counts it
    # (previously market_id was None, making it invisible to the §3.2 double-count guard).
    market = _seed_market(session, "mkt-queued")
    repo = CandidateSignalRepo(session)
    repo.enqueue(
        CandidateSignal(
            id=_uid("cs-queued-only"),
            market_id=market,
            asof=_TS,
            trader_ids=[_uid("t")],
            source=CandidateSource.NEW_ENTRY,
            status=CandidateSignalStatus.QUEUED,
            created_at=_TS,
        )
    )
    assert repo.has_market_trigger(market) is True


class _EmptyBase:
    def retrieve(self, market_id: str, asof: datetime, k: int) -> list[Evidence]:
        return []


class _FlowStub:
    def active_on_market(self, market_id: str, *, asof: datetime) -> list[TraderEntry]:
        return [
            TraderEntry(
                id=_uid(f"flow-{market_id}"),
                trader_id=_uid("t"),
                external_market="0xext",
                market_id=market_id,
                side="YES",
                ts=asof,
                is_increase=True,
            )
        ]


def test_queued_trigger_blocks_smart_money_evidence_end_to_end(session: Session) -> None:
    # PYT-117/R-8 end-to-end through the runner is_triggered wiring, evidence feature ON: a market
    # whose only smart-money signal is a still-QUEUED trigger must not also receive flow evidence.
    triggered = _seed_market(session, "mkt-q-trig")
    untriggered = _seed_market(session, "mkt-q-untrig")
    CandidateSignalRepo(session).enqueue(
        CandidateSignal(
            id=_uid("cs-q"),
            market_id=triggered,
            asof=_TS,
            trader_ids=[_uid("t")],
            source=CandidateSource.NEW_ENTRY,
            status=CandidateSignalStatus.QUEUED,  # over-cap, not evaluated — yet a real trigger
            created_at=_TS,
        )
    )
    port = SmartMoneyEvidencePort(
        _EmptyBase(),
        flow=_FlowStub(),
        triggers=_RepoTriggeredMarkets(session),
        enabled=True,
    )

    # Control: an untriggered market still receives flow → the mechanism is live (non-vacuous).
    untrig = port.retrieve(untriggered, _TS, 8)
    assert any(e.source_id == SMART_MONEY_SOURCE_ID for e in untrig)
    # The QUEUED-triggered market does not — the deferred trigger closes the double-count window.
    trig = port.retrieve(triggered, _TS, 8)
    assert not any(e.source_id == SMART_MONEY_SOURCE_ID for e in trig)


def test_roster_event_is_append_only(session: Session) -> None:
    tid = _seed_trader(session)
    repo = RosterEventRepo(session)
    event = repo.add(
        RosterEvent(
            id=_uid("re"),
            trader_id=tid,
            to_state=TraderState.ACTIVE,
            reason="promoted: agreed_bpd 0.18 over n=42",
            created_at=_TS,
            from_state=TraderState.TRIAL,
        )
    )
    assert [e.id for e in repo.list_for_trader(tid)] == [event.id]
    with pytest.raises(DBAPIError):
        session.execute(
            update(models.RosterEvent)
            .where(col(models.RosterEvent.id) == uuid.UUID(event.id))
            .values(reason="tampered")
        )
    session.rollback()
