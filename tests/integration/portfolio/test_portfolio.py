"""Portfolio capital state on real Postgres: live available_capital + capital-aware sizing."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.common import (
    CriticVerdict,
    Estimate,
    EstimationContext,
    Evidence,
    Market,
    MarketSnapshot,
    PaperTrade,
    Side,
    Source,
    TradeStatus,
)
from pythia.config import Settings
from pythia.db.repositories import (
    EstimateRepository,
    MarketRepository,
    SnapshotRepository,
    SourceRepository,
)
from pythia.orchestration import Pipeline
from pythia.paper import PaperLedger
from pythia.portfolio.service import PortfolioService
from pythia.rag import EvidenceStore, StubEmbeddingPort
from pythia.risk import RiskReason

pytestmark = pytest.mark.integration

AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
SOURCE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "portfolio-source"))


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _seed_market(session: Session, name: str, category: str) -> tuple[Market, MarketSnapshot]:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, name))
    market = MarketRepository(session).upsert(
        Market(
            id=market_id, external_id=name, question="q", category=category, resolution_rule="UMA"
        )
    )
    snapshot = SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{name}-snap")),
            market_id=market_id,
            ts=AT,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=5000.0,
        )
    )
    SourceRepository(session).upsert(
        Source(id=SOURCE_ID, name="reuters", kind="news", reliability=0.9)
    )
    EvidenceStore(session, StubEmbeddingPort()).add(
        market_id=market_id,
        source_id=SOURCE_ID,
        observed_at=AT - timedelta(days=1),
        content=f"evidence for {name}",
    )
    return market, snapshot


def _open_position(session: Session, name: str, category: str, size: float) -> None:
    market, snapshot = _seed_market(session, name, category)
    estimate_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{name}-est"))
    EstimateRepository(session).add(
        Estimate(
            id=estimate_id,
            market_id=market.id,
            snapshot_id=snapshot.id,
            p_model=0.5,
            confidence=0.5,
            lower=0.4,
            upper=0.6,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
        )
    )
    PaperLedger(session).open(
        PaperTrade(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{name}-trade")),
            market_id=market.id,
            estimate_id=estimate_id,
            side=Side.YES,
            entry_price=0.5,
            size=size,
            status=TradeStatus.OPEN,
            opened_at=AT,
        )
    )


class _ConfidentLLM:
    def __init__(self) -> None:
        self.estimate_calls = 0

    def estimate(self, ctx: EstimationContext) -> Estimate:
        self.estimate_calls += 1
        return Estimate(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"conf-{ctx.snapshot.id}")),
            market_id=ctx.market.id,
            snapshot_id=ctx.snapshot.id,
            p_model=0.7,
            confidence=0.9,
            lower=0.6,
            upper=0.8,
            rationale="c",
            evidence_ids=[item.id for item in ctx.evidence],
            model_name="fake",
        )

    def critique(self, est: Estimate, evidence: list[Evidence]) -> CriticVerdict:
        return CriticVerdict(
            adjusted_confidence=0.8, objections=[], lookahead_leak=False, notes="ok"
        )


def _pipeline(session: Session, llm: object) -> Pipeline:
    return Pipeline(
        session=session,
        llm=llm,  # type: ignore[arg-type]
        evidence=EvidenceStore(session, StubEmbeddingPort()),
        settings=_settings(),
        routing={"estimation": "x", "critic": "x"},
    )


def test_available_capital_reflects_open_positions(session: Session) -> None:
    _open_position(session, "p1", "politics", 1500.0)
    service = PortfolioService(session, _settings())
    assert service.locked_in_open() == pytest.approx(1500.0)
    assert service.available_capital() == pytest.approx(10_000.0 - 1500.0)
    assert service.exposure_state().per_category["politics"] == pytest.approx(1500.0)


def test_pipeline_vetoes_a_category_at_cap_before_any_model_call(session: Session) -> None:
    _open_position(session, "p1", "politics", 2000.0)
    market, snapshot = _seed_market(session, "p2", "politics")
    llm = _ConfidentLLM()
    result = _pipeline(session, llm).run(market, snapshot)

    assert result.estimate is None
    assert result.decision.vetoed
    assert RiskReason.CAP_PER_CATEGORY in result.decision.reasons
    assert llm.estimate_calls == 0


def test_pipeline_sizes_against_live_capital(session: Session) -> None:
    market_a, snapshot_a = _seed_market(session, "a", "alpha")
    full = _pipeline(session, _ConfidentLLM()).run(market_a, snapshot_a)

    _open_position(session, "lock", "beta", 8000.0)
    market_b, snapshot_b = _seed_market(session, "b", "gamma")
    reduced = _pipeline(session, _ConfidentLLM()).run(market_b, snapshot_b)

    assert not full.decision.vetoed
    assert not reduced.decision.vetoed
    assert reduced.decision.approved_size < full.decision.approved_size
