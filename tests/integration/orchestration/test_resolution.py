"""Resolution handling on real Postgres (§5.5, AC-5/AC-16): record, audit, settle, post-mortem."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.common import (
    AuditKind,
    Estimate,
    FailureClass,
    Market,
    MarketSnapshot,
    PaperTrade,
    Side,
    TradeStatus,
)
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import (
    AuditRepository,
    EstimateRepository,
    MarketRepository,
    PostmortemRepository,
    SnapshotRepository,
)
from pythia.eval.service import CalibrationService
from pythia.orchestration import ResolutionService
from pythia.paper import PaperLedger

pytestmark = pytest.mark.integration

AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def _seed(session: Session, name: str, *, p_model: float, confidence: float) -> Estimate:
    market_id = _uid(name)
    snapshot_id = _uid(f"{name}-snap")
    MarketRepository(session).upsert(
        Market(
            id=market_id, external_id=name, question="q", category="politics", resolution_rule="r"
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=snapshot_id,
            market_id=market_id,
            ts=AT,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    return EstimateRepository(session).add(
        Estimate(
            id=_uid(f"{name}-est"),
            market_id=market_id,
            snapshot_id=snapshot_id,
            p_model=p_model,
            confidence=confidence,
            lower=max(0.0, p_model - 0.1),
            upper=min(1.0, p_model + 0.1),
            rationale="r",
            evidence_ids=[],
            model_name="stub",
        )
    )


def _open_trade(session: Session, estimate: Estimate) -> None:
    PaperLedger(session).open(
        PaperTrade(
            id=_uid(f"{estimate.id}-trade"),
            market_id=estimate.market_id,
            estimate_id=estimate.id,
            side=Side.YES,
            entry_price=0.5,
            size=100.0,
            status=TradeStatus.OPEN,
            opened_at=AT,
        )
    )


def _service(session: Session) -> ResolutionService:
    return ResolutionService(session, _settings(), PaperLedger(session))


def test_record_audits_settles_and_post_mortems_a_surprise(session: Session) -> None:
    estimate = _seed(session, "surprise", p_model=0.95, confidence=0.9)
    _open_trade(session, estimate)

    postmortem = _service(session).record(estimate.market_id, outcome=0, at=AT + timedelta(days=5))

    # resolution recorded + market flagged resolved
    market = MarketRepository(session).get(estimate.market_id)
    assert market is not None and market.resolved is True

    # RESOLUTION audit event
    kinds = {event.kind for event in AuditRepository(session).list_for_market(estimate.market_id)}
    assert AuditKind.RESOLUTION in kinds

    # paper trade settled with PnL
    trade = session.get(models.PaperTrade, uuid.UUID(_uid(f"{estimate.id}-trade")))
    assert trade is not None and trade.status == TradeStatus.RESOLVED and trade.pnl is not None

    # surprising miss → post-mortem, linked to the resolution, storing the raw Brier
    assert postmortem is not None
    assert postmortem.category == "politics"
    assert postmortem.resolution_id is not None
    assert postmortem.brier == pytest.approx((0.95 - 0) ** 2)
    assert len(PostmortemRepository(session).list_for_market(estimate.market_id)) == 1


def test_calibrated_loss_settles_but_writes_no_post_mortem(session: Session) -> None:
    estimate = _seed(session, "calibrated", p_model=0.7, confidence=0.7)
    _open_trade(session, estimate)

    postmortem = _service(session).record(estimate.market_id, outcome=0, at=AT + timedelta(days=5))

    assert postmortem is None
    assert PostmortemRepository(session).list_for_market(estimate.market_id) == []
    # still audited and settled
    kinds = {event.kind for event in AuditRepository(session).list_for_market(estimate.market_id)}
    assert AuditKind.RESOLUTION in kinds
    trade = session.get(models.PaperTrade, uuid.UUID(_uid(f"{estimate.id}-trade")))
    assert trade is not None and trade.status == TradeStatus.RESOLVED


def test_ambiguous_resolution_is_attributed_as_resolution_ambiguity(session: Session) -> None:
    estimate = _seed(session, "ambiguous", p_model=0.95, confidence=0.9)

    postmortem = _service(session).record(
        estimate.market_id, outcome=0, at=AT + timedelta(days=5), ambiguous=True
    )

    assert postmortem is not None
    assert postmortem.failure_class == FailureClass.RESOLUTION_AMBIGUITY


def test_category_brier_deltas_are_ordered_for_drift(session: Session) -> None:
    first = _seed(session, "d1", p_model=0.8, confidence=0.8)
    second = _seed(session, "d2", p_model=0.2, confidence=0.8)
    service = _service(session)
    service.record(first.market_id, outcome=1, at=AT + timedelta(days=1))
    service.record(second.market_id, outcome=1, at=AT + timedelta(days=2))

    deltas = CalibrationService(session).category_brier_deltas("politics")
    assert len(deltas) == 2  # one per non-ambiguous resolution, in resolution order
