"""Integration test (PYT-96, AC-S3): point-in-time trader scoring on real Postgres.

A wallet's scorecard uses only entries whose market resolved BEFORE asof — a resolution dated after
asof must never leak in (no lookahead) — and carries both realized edge and pipeline agreement.
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session

from pythia.agents import StubLLMPort
from pythia.common import (
    CriticVerdict,
    Estimate,
    EstimationContext,
    Evidence,
    Market,
    MarketSnapshot,
    ResolutionProposal,
    TrackedTrader,
    TraderEntry,
    TraderState,
)
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


def _evidence(session: Session) -> EvidenceStore:
    return EvidenceStore(session, StubEmbeddingPort())


class _CriticControlledLLM:
    """Stub estimator with a configurable critic verdict, to exercise the agreement gate."""

    def __init__(self, *, p_model: float, lookahead_leak: bool, adjusted_confidence: float) -> None:
        self._p_model = p_model
        self._leak = lookahead_leak
        self._adjusted = adjusted_confidence
        self._stub = StubLLMPort()

    def estimate(self, ctx: EstimationContext) -> Estimate:
        return Estimate(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"est-{ctx.snapshot.id}")),
            market_id=ctx.market.id,
            snapshot_id=ctx.snapshot.id,
            p_model=self._p_model,
            confidence=0.9,
            lower=0.0,
            upper=1.0,
            rationale="t",
            evidence_ids=[],
            model_name="fake",
        )

    def critique(self, est: Estimate, evidence: list[Evidence]) -> CriticVerdict:
        return CriticVerdict(
            adjusted_confidence=self._adjusted,
            objections=[],
            lookahead_leak=self._leak,
            notes="",
        )

    def discover(self, markets: list[Market], limit: int) -> list[str]:
        return self._stub.discover(markets, limit)

    def grade_resolution(self, market: Market, evidence: list[Evidence]) -> ResolutionProposal:
        return self._stub.grade_resolution(market, evidence)


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

    returns, categories, entry_prices = FalconTraderReturns(session, _falcon()).resolved(
        trader, _ASOF
    )

    assert categories == ["politics"]  # only market A; B resolved after asof (no lookahead)
    assert returns == [pytest.approx((1.0 - 0.4) / 0.4)]  # side "Up" == winning_outcome → a win
    assert entry_prices == [pytest.approx(0.4)]  # the wallet's entry price, for the scalper guard


def test_agreement_replays_only_resolved_before_asof(session: Session) -> None:
    trader = _trader(session)
    _seed_resolved_entry(
        session, trader.id, name="0xA", resolved_at=_ASOF - timedelta(days=1), side=_WINNING
    )
    _seed_resolved_entry(
        session, trader.id, name="0xB", resolved_at=_ASOF + timedelta(days=1), side=_WINNING
    )

    evaluator = PipelineAgreementEvaluator(
        session, StubLLMPort(), _evidence(session), min_confidence=_settings().min_confidence
    )
    agree_rate, agreed_bpd = evaluator.agreement(trader, _ASOF)

    assert agree_rate in (0.0, 1.0)  # exactly one entry replayed → agreed or not, never a fraction
    assert isinstance(agreed_bpd, float)


def test_should_count_agreement_when_critic_keeps_confidence(session: Session) -> None:
    trader = _trader(session)
    _seed_resolved_entry(
        session, trader.id, name="0xA", resolved_at=_ASOF - timedelta(days=1), side=_WINNING
    )
    # p_model 0.9 vs market 0.5 → positive edge_net; critic keeps confidence above min_confidence.
    llm = _CriticControlledLLM(p_model=0.9, lookahead_leak=False, adjusted_confidence=0.9)
    evaluator = PipelineAgreementEvaluator(session, llm, _evidence(session), min_confidence=0.5)

    agree_rate, agreed_bpd = evaluator.agreement(trader, _ASOF)

    assert agree_rate == 1.0
    assert agreed_bpd == pytest.approx((0.5 - 1) ** 2 - (0.9 - 1) ** 2)


def test_should_drop_agreement_when_critic_flags_lookahead(session: Session) -> None:
    trader = _trader(session)
    _seed_resolved_entry(
        session, trader.id, name="0xA", resolved_at=_ASOF - timedelta(days=1), side=_WINNING
    )
    # Same positive edge, but the critic flags a lookahead leak → confidence 0 → the live system
    # would not act, so the entry must not count toward agreement.
    llm = _CriticControlledLLM(p_model=0.9, lookahead_leak=True, adjusted_confidence=0.9)
    evaluator = PipelineAgreementEvaluator(session, llm, _evidence(session), min_confidence=0.5)

    agree_rate, agreed_bpd = evaluator.agreement(trader, _ASOF)

    assert agree_rate == 0.0
    assert agreed_bpd == 0.0


def test_harness_writes_a_point_in_time_scorecard(session: Session) -> None:
    trader = _trader(session)
    _seed_resolved_entry(
        session, trader.id, name="0xA", resolved_at=_ASOF - timedelta(days=1), side=_WINNING
    )
    _seed_resolved_entry(
        session, trader.id, name="0xB", resolved_at=_ASOF + timedelta(days=1), side=_WINNING
    )

    harness = build_selection_harness(
        session,
        falcon=_falcon(),
        llm=StubLLMPort(),
        evidence=_evidence(session),
        settings=_settings(),
    )
    cards = harness.review([trader], asof=_ASOF)

    assert len(cards) == 1
    card = cards[0]
    assert card.n_resolved == 1  # only the before-asof resolution
    assert card.market_class_ok is True  # politics is on the allow-list
    assert card.agree_rate is not None and card.roi is not None
