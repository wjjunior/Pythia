"""Integration tests for the smart-money dashboard API (PYT-92, AC-S6)."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from pythia.api.app import create_app
from pythia.api.deps import get_session, settings_dependency
from pythia.common import (
    CandidateSignal,
    CandidateSignalStatus,
    CandidateSource,
    Estimate,
    HeldPosition,
    Market,
    MarketSnapshot,
    RosterEvent,
    Score,
    SignalSource,
    TrackedTrader,
    TraderEntry,
    TraderScorecard,
    TraderState,
)
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import (
    EstimateRepository,
    MarketRepository,
    ScoreRepository,
    SnapshotRepository,
)
from pythia.db.smartmoney import (
    CandidateSignalRepo,
    RosterEventRepo,
    TrackedTraderRepo,
    TraderEntryRepo,
    TraderPositionRepo,
    TraderScorecardRepo,
)

pytestmark = pytest.mark.integration

_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def app(session: Session) -> FastAPI:
    application = create_app()
    application.dependency_overrides[get_session] = lambda: session
    application.dependency_overrides[settings_dependency] = lambda: Settings(
        _env_file=None, database_url="postgresql+psycopg://x/y"
    )
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def _trader(
    session: Session, *, wallet: str = "0xw", state: TraderState = TraderState.TRIAL
) -> TrackedTrader:
    return TrackedTraderRepo(session).add(
        TrackedTrader(id=_uid(wallet), wallet=wallet, state=state, added_at=_TS, state_since=_TS)
    )


def test_roster_returns_trader_scorecard_entry_and_reason(
    client: TestClient, session: Session
) -> None:
    trader = _trader(session)
    TraderScorecardRepo(session).add(
        TraderScorecard(
            id=_uid("sc"),
            trader_id=trader.id,
            asof=_TS,
            n_resolved=5,
            market_class_ok=True,
            trader_score=float("-inf"),  # a hard-floored trial — must serialize as null
        )
    )
    TraderEntryRepo(session).add(
        TraderEntry(
            id=_uid("e"),
            trader_id=trader.id,
            external_market="0xmkt",
            side="YES",
            ts=_TS,
            is_increase=True,
        )
    )
    RosterEventRepo(session).add(
        RosterEvent(
            id=_uid("re"),
            trader_id=trader.id,
            to_state=TraderState.TRIAL,
            reason="discovered: leaderboard rank 1",
            created_at=_TS,
        )
    )

    item = client.get("/api/smartmoney/roster").json()[0]

    assert item["trader"]["wallet"] == "0xw" and item["trader"]["state"] == "trial"
    assert item["scorecard"]["trader_score"] is None  # -inf sanitized → null (valid JSON)
    assert item["scorecard"]["n_resolved"] == 5
    assert item["last_entry"]["external_market"] == "0xmkt"
    assert "discovered" in item["reason"]


def test_candidate_feed_includes_the_pipeline_verdict(client: TestClient, session: Session) -> None:
    market = MarketRepository(session).upsert(
        Market(
            id=_uid("m"),
            external_id="0xext",
            question="Q?",
            category="politics",
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=_uid("s"),
            market_id=market.id,
            ts=_TS,
            yes_price=0.4,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    estimate = EstimateRepository(session).add(
        Estimate(
            id=_uid("est"),
            market_id=market.id,
            snapshot_id=_uid("s"),
            p_model=0.6,
            confidence=0.8,
            lower=0.5,
            upper=0.7,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
            signal_source=SignalSource.UNKNOWN,
        )
    )
    ScoreRepository(session).add(
        Score(
            estimate_id=estimate.id,
            edge=0.2,
            edge_net=0.19,
            ev_unit=0.19,
            p_resol=0.6,
            p_liq=1.0,
            p_time=1.0,
            p_corr=1.0,
            opportunity_score=0.09,
            kelly_fraction=0.3,
            signal_source=SignalSource.UNKNOWN,
        )
    )
    trader = _trader(session)
    CandidateSignalRepo(session).enqueue(
        CandidateSignal(
            id=_uid("cs"),
            market_id=market.id,
            asof=_TS,
            trader_ids=[trader.id],
            source=CandidateSource.NEW_ENTRY,
            status=CandidateSignalStatus.EVALUATED,
            created_at=_TS,
        )
    )

    item = client.get("/api/smartmoney/candidates").json()[0]

    assert item["viable"] is True and item["opportunity_score"] == pytest.approx(0.09)
    assert item["recommended_side"] == "YES" and item["market"]["question"] == "Q?"
    assert item["candidate"]["trader_ids"] == [trader.id]
    assert item["review_status"] == "none"  # not yet reviewed (PYT-154)


def test_candidate_feed_flags_an_open_review_state(client: TestClient, session: Session) -> None:
    market = MarketRepository(session).upsert(
        Market(id=_uid("rm"), external_id="0xrm", question="R?", category="c", resolution_rule="r")
    )
    session.add(
        models.PaperTrade(
            id=uuid.uuid4(),
            market_id=uuid.UUID(market.id),
            estimate_id=None,
            side="YES",
            entry_price=0.5,
            size=1.0,
            status="open",
            opened_at=_TS,
        )
    )
    session.commit()
    trader = _trader(session, wallet="0xrw")
    CandidateSignalRepo(session).enqueue(
        CandidateSignal(
            id=_uid("rcs"),
            market_id=market.id,
            asof=_TS,
            trader_ids=[trader.id],
            source=CandidateSource.NEW_ENTRY,
            status=CandidateSignalStatus.EVALUATED,
            created_at=_TS,
        )
    )

    item = client.get("/api/smartmoney/candidates").json()[0]
    assert item["review_status"] == "open"  # the candidate's market has an open paper position


def test_candidate_feed_excludes_skipped_untracked_candidates(
    client: TestClient, session: Session
) -> None:
    trader = _trader(session, wallet="0xskip")
    CandidateSignalRepo(session).enqueue(
        CandidateSignal(
            id=_uid("skip"),
            market_id=None,  # an untracked (closed/resolved) market — never entered the pipeline
            asof=_TS,
            trader_ids=[trader.id],
            source=CandidateSource.NEW_ENTRY,
            status=CandidateSignalStatus.SKIPPED,
            created_at=_TS,
        )
    )

    assert client.get("/api/smartmoney/candidates").json() == []  # SKIPPED is not a feed row


def test_roster_events_timeline_carries_wallet_and_reason(
    client: TestClient, session: Session
) -> None:
    trader = _trader(session, wallet="0xw2")
    RosterEventRepo(session).add(
        RosterEvent(
            id=_uid("re2"),
            trader_id=trader.id,
            from_state=TraderState.TRIAL,
            to_state=TraderState.ACTIVE,
            reason="promoted: score 1.5",
            created_at=_TS,
        )
    )

    item = client.get("/api/smartmoney/roster-events").json()[0]

    assert item["event"]["to_state"] == "active" and item["wallet"] == "0xw2"
    assert "promoted" in item["event"]["reason"]


def test_override_pins_a_trader_and_audits_it(client: TestClient, session: Session) -> None:
    trader = _trader(session, wallet="0xw3", state=TraderState.BENCHED)

    pinned = client.post(
        f"/api/smartmoney/traders/{trader.id}/override", json={"action": "pin"}
    ).json()

    assert pinned["trader"]["state"] == "active" and pinned["reason"] == "manual"
    events = client.get("/api/smartmoney/roster-events").json()
    manual = [e for e in events if e["event"]["reason"] == "manual"]
    assert len(manual) == 1 and manual[0]["event"]["to_state"] == "active"


def test_positions_endpoint_returns_holdings_with_wallet(
    client: TestClient, session: Session
) -> None:
    trader = _trader(session, wallet="0xholder", state=TraderState.ACTIVE)
    TraderPositionRepo(session).replace_for_trader(
        trader.id,
        [
            HeldPosition(
                condition_id="0xc1",
                outcome="Yes",
                size=100.0,
                avg_price=0.4,
                cur_price=0.55,
                current_value=55.0,
                cash_pnl=15.0,
                title="Will it resolve YES?",
                end_date=_TS,
            )
        ],
        asof=_TS,
    )

    item = client.get("/api/smartmoney/positions").json()[0]

    assert item["wallet"] == "0xholder"
    assert item["position"]["condition_id"] == "0xc1" and item["position"]["outcome"] == "Yes"
    assert item["position"]["cash_pnl"] == 15.0
    assert item["position"]["title"] == "Will it resolve YES?"


def test_positions_endpoint_omits_retired_wallets(client: TestClient, session: Session) -> None:
    trader = _trader(session, wallet="0xretiree", state=TraderState.RETIRED)
    TraderPositionRepo(session).replace_for_trader(
        trader.id,
        [
            HeldPosition(
                condition_id="0xc9",
                outcome="No",
                size=1.0,
                avg_price=0.5,
                cur_price=0.5,
                current_value=0.5,
                cash_pnl=0.0,
                title="stale",
                end_date=_TS,
            )
        ],
        asof=_TS,
    )

    assert client.get("/api/smartmoney/positions").json() == []  # retired ⇒ not a current holding


def test_override_unknown_trader_is_404(client: TestClient) -> None:
    missing = str(uuid.uuid5(uuid.NAMESPACE_URL, "nope"))
    assert (
        client.post(
            f"/api/smartmoney/traders/{missing}/override", json={"action": "mute"}
        ).status_code
        == 404
    )
