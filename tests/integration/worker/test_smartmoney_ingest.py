"""Integration test (PYT-97): the smart-money worker loop on real Postgres + Falcon fixtures.

Proves AC-S2 (detection → pipeline verdict), AC-S4 (discover → score → roster events), and fail-soft
(Falcon down ⇒ no candidates, no crash, v1 path untouched).
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session

from pythia.common import (
    CandidateSignal,
    CandidateSignalStatus,
    CandidateSource,
    Market,
    MarketSnapshot,
    TrackedTrader,
    TraderState,
)
from pythia.config import Settings
from pythia.db.repositories import EstimateRepository, MarketRepository, SnapshotRepository
from pythia.db.smartmoney import CandidateSignalRepo, RosterEventRepo, TrackedTraderRepo
from pythia.ingestion.falcon import FalconClient, FixtureFalconTransport
from pythia.ingestion.falcon.client import FalconDataset
from pythia.ingestion.market_crawler import TransientTransportError
from pythia.worker.smartmoney_ingest import run_detection, run_roster_review

pytestmark = pytest.mark.integration

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "falcon"
_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
# The condition_id both trades in trader_trades.json carry (BUY ⇒ a new-entry trigger).
_TRADED_COND = "0x584654c869a3f1868f3ad1248e11ea988f3c4e332002839c6cac0e6db94485ee"


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _falcon() -> FalconClient:
    return FalconClient(FixtureFalconTransport(_FIXTURES))


class _RaisingTransport:
    def retrieve(self, dataset: FalconDataset, params: dict[str, Any]) -> Any:
        raise TransientTransportError("falcon down")


def _dead_falcon() -> FalconClient:
    return FalconClient(_RaisingTransport())


def _active_trader(session: Session) -> TrackedTrader:
    return TrackedTraderRepo(session).add(
        TrackedTrader(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "smi-w")),
            wallet="0xwhale",
            state=TraderState.ACTIVE,
            added_at=_NOW,
            state_since=_NOW,
        )
    )


def _seed_traded_market(session: Session) -> str:
    market = MarketRepository(session).upsert(
        Market(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "smi-mkt")),
            external_id=_TRADED_COND,
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "smi-snap")),
            market_id=market.id,
            ts=_NOW,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    return market.id


def test_detection_tick_turns_an_entry_into_a_paper_verdict(session: Session) -> None:
    _active_trader(session)
    market_id = _seed_traded_market(session)

    run_detection(session, _settings(), _falcon(), now=_NOW)

    signals = CandidateSignalRepo(session).recent(10, now=_NOW)
    assert len(signals) == 1  # two fixture trades on one market → one merged candidate (AC-S2)
    assert signals[0].status is CandidateSignalStatus.EVALUATED
    assert EstimateRepository(session).latest_for_market(market_id) is not None  # verdict persisted


def test_roster_review_discovers_scores_and_audits(session: Session) -> None:
    run_roster_review(session, _settings(), _falcon(), now=_NOW)

    traders = TrackedTraderRepo(session).list()
    assert len(traders) == 3  # three leaderboard wallets enter as trial (AC-S4)
    assert all(t.state is TraderState.TRIAL for t in traders)
    events = RosterEventRepo(session).recent(10)
    assert len(events) >= 3  # each discovery is audited with a reason


def _enqueue_candidate(
    session: Session, key: str, *, resolved: bool = False, close_time: datetime | None = None
) -> str:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"cand-{key}"))
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=f"cand-{key}",
            question="q",
            category="politics",
            resolution_rule="r",
            resolved=resolved,
            close_time=close_time,
        )
    )
    CandidateSignalRepo(session).enqueue(
        CandidateSignal(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"sig-{key}")),
            market_id=market_id,
            asof=_NOW,
            trader_ids=[str(uuid.uuid5(uuid.NAMESPACE_URL, "t"))],
            source=CandidateSource.NEW_ENTRY,
            status=CandidateSignalStatus.EVALUATED,
            created_at=_NOW,
        )
    )
    return market_id


def test_candidate_feed_hides_resolved_and_closed_markets(session: Session) -> None:
    live = _enqueue_candidate(session, "live")
    _enqueue_candidate(session, "resolved", resolved=True)
    _enqueue_candidate(session, "closed", close_time=_NOW - timedelta(hours=1))

    feed = CandidateSignalRepo(session).recent(10, now=_NOW)

    assert [c.market_id for c in feed] == [live]  # resolved + closed are hidden (PYT-175)


def test_detection_is_fail_soft_when_falcon_is_down(session: Session) -> None:
    _active_trader(session)
    _seed_traded_market(session)

    run_detection(session, _settings(), _dead_falcon(), now=_NOW)  # must not raise

    assert (
        CandidateSignalRepo(session).recent(10, now=_NOW) == []
    )  # no candidates; v1 path untouched


def test_roster_review_is_fail_soft_when_falcon_is_down(session: Session) -> None:
    run_roster_review(session, _settings(), _dead_falcon(), now=_NOW)  # must not raise

    assert TrackedTraderRepo(session).list() == []  # nothing discovered, no crash
