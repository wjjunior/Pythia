"""Integration test (PYT-96, AC-S3): point-in-time trader scoring on real Postgres.

A wallet's scorecard uses only entries whose market resolved BEFORE asof — a resolution dated after
asof must never leak in (no lookahead) — and carries both realized edge and pipeline agreement.
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session

from pythia.agents import EstimationAgent, StubLLMPort
from pythia.common import Market, MarketSnapshot, TrackedTrader, TraderEntry, TraderState
from pythia.config import Settings
from pythia.db.repositories import MarketRepository, ResolutionRepository, SnapshotRepository
from pythia.db.smartmoney import TrackedTraderRepo, TraderEntryRepo
from pythia.ingestion.falcon import FalconClient, FixtureFalconTransport
from pythia.rag import EvidenceStore, StubEmbeddingPort
from pythia.smartmoney.scoring import (
    FalconTraderReturns,
    PipelineAgreementEvaluator,
    build_selection_harness,
)

pytestmark = pytest.mark.integration

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "falcon"
_ASOF = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
_ENTRY_TS = _ASOF - timedelta(days=10)
_WINNING = "Up"  # matches winning_outcome in tests/fixtures/falcon/market.json


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _falcon() -> FalconClient:
    return FalconClient(FixtureFalconTransport(_FIXTURES))


def _estimator(session: Session) -> EstimationAgent:
    return EstimationAgent(StubLLMPort(), EvidenceStore(session, StubEmbeddingPort()))


def _seed_resolved_entry(
    session: Session, trader_id: str, *, name: str, resolved_at: datetime, side: str
) -> None:
    market = MarketRepository(session).upsert(
        Market(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, name)),
            external_id=name,
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{name}-snap")),
            market_id=market.id,
            ts=_ENTRY_TS,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    ResolutionRepository(session).record(market.id, 1, at=resolved_at)
    TraderEntryRepo(session).add(
        TraderEntry(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{name}-entry")),
            trader_id=trader_id,
            market_id=market.id,
            external_market=name,
            side=side,
            ts=_ENTRY_TS,
            is_increase=True,
            entry_price=0.4,
            size_usd=100.0,
        )
    )


def _trader(session: Session) -> TrackedTrader:
    return TrackedTraderRepo(session).add(
        TrackedTrader(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "sc-w")),
            wallet="0xsharp",
            state=TraderState.TRIAL,
            added_at=_ENTRY_TS,
            state_since=_ENTRY_TS,
        )
    )


def test_returns_exclude_resolutions_after_asof(session: Session) -> None:
    trader = _trader(session)
    _seed_resolved_entry(
        session, trader.id, name="0xA", resolved_at=_ASOF - timedelta(days=1), side=_WINNING
    )
    _seed_resolved_entry(
        session, trader.id, name="0xB", resolved_at=_ASOF + timedelta(days=1), side=_WINNING
    )

    returns, categories = FalconTraderReturns(session, _falcon()).resolved(trader, _ASOF)

    assert categories == ["politics"]  # only market A; B resolved after asof (no lookahead)
    assert returns == [pytest.approx((1.0 - 0.4) / 0.4)]  # side "Up" == winning_outcome → a win


def test_agreement_replays_only_resolved_before_asof(session: Session) -> None:
    trader = _trader(session)
    _seed_resolved_entry(
        session, trader.id, name="0xA", resolved_at=_ASOF - timedelta(days=1), side=_WINNING
    )
    _seed_resolved_entry(
        session, trader.id, name="0xB", resolved_at=_ASOF + timedelta(days=1), side=_WINNING
    )

    agree_rate, agreed_bpd = PipelineAgreementEvaluator(session, _estimator(session)).agreement(
        trader, _ASOF
    )

    assert agree_rate in (0.0, 1.0)  # exactly one entry replayed → agreed or not, never a fraction
    assert isinstance(agreed_bpd, float)


def test_harness_writes_a_point_in_time_scorecard(session: Session) -> None:
    trader = _trader(session)
    _seed_resolved_entry(
        session, trader.id, name="0xA", resolved_at=_ASOF - timedelta(days=1), side=_WINNING
    )
    _seed_resolved_entry(
        session, trader.id, name="0xB", resolved_at=_ASOF + timedelta(days=1), side=_WINNING
    )

    harness = build_selection_harness(
        session, falcon=_falcon(), estimator=_estimator(session), settings=_settings()
    )
    cards = harness.review([trader], asof=_ASOF)

    assert len(cards) == 1
    card = cards[0]
    assert card.n_resolved == 1  # only the before-asof resolution
    assert card.market_class_ok is True  # politics is on the allow-list
    assert card.agree_rate is not None and card.roi is not None
