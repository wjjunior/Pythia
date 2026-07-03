"""Human review gate against real Postgres: approve → paper, reject → terminal, both audited."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session, select

from pythia.common import (
    AuditEvent,
    AuditKind,
    Estimate,
    Market,
    MarketSnapshot,
    Score,
    Side,
    SignalSource,
    TradeStatus,
)
from pythia.config import Settings
from pythia.config.settings import ExecutionModeError
from pythia.db import models
from pythia.db.repositories import (
    AuditRepository,
    EstimateRepository,
    MarketRepository,
    ScoreRepository,
    SnapshotRepository,
)
from pythia.orchestration import (
    NotRiskEvaluatedError,
    NotScoredError,
    ReviewGate,
    UnknownEstimateError,
    VetoedOpportunityError,
)
from pythia.paper import PaperLedger, fill_price
from pythia.risk.models import RiskDecision, RiskReason

pytestmark = pytest.mark.integration

MARKET_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt23-market"))
SNAPSHOT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt23-snapshot"))
ESTIMATE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt23-estimate"))
AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
YES_PRICE = 0.6
SPREAD = 0.04


def _settings(*, mode: str = "paper") -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y", mode=mode)


def _ok_decision(approved_size: float = 1000.0) -> RiskDecision:
    return RiskDecision(approved_size=approved_size, vetoed=False, reasons=(), paper_only=True)


def _seed(session: Session, *, with_score: bool = True, risk: RiskDecision | None = None) -> None:
    MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt23",
            question="Will it resolve YES?",
            category="politics",
            resolution_rule="rule",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=SNAPSHOT_ID,
            market_id=MARKET_ID,
            ts=AT,
            yes_price=YES_PRICE,
            spread=SPREAD,
            volume=1.0,
            liquidity=1.0,
        )
    )
    EstimateRepository(session).add(
        Estimate(
            id=ESTIMATE_ID,
            market_id=MARKET_ID,
            snapshot_id=SNAPSHOT_ID,
            p_model=0.7,
            confidence=0.8,
            lower=0.6,
            upper=0.8,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
            signal_source=SignalSource.UNKNOWN,
        )
    )
    if with_score:
        ScoreRepository(session).add(
            Score(
                estimate_id=ESTIMATE_ID,
                edge=0.1,
                edge_net=0.08,
                ev_unit=0.08,
                p_resol=0.6,
                p_liq=1.0,
                p_time=1.0,
                p_corr=1.0,
                opportunity_score=0.0384,
                kelly_fraction=0.2,
                signal_source=SignalSource.UNKNOWN,
            )
        )
    if risk is not None:
        AuditRepository(session).append(
            AuditEvent(
                kind=AuditKind.RISK_DECISION,
                market_id=MARKET_ID,
                payload={
                    "estimate_id": ESTIMATE_ID,
                    "approved_size": risk.approved_size,
                    "vetoed": risk.vetoed,
                    "paper_only": risk.paper_only,
                    "reasons": [reason.value for reason in risk.reasons],
                },
            )
        )


def _gate(session: Session, *, mode: str = "paper") -> ReviewGate:
    return ReviewGate(session, PaperLedger(session), _settings(mode=mode))


def test_approve_records_a_paper_trade_at_the_observed_price(session: Session) -> None:
    _seed(session, risk=_ok_decision())
    trade = _gate(session).approve(ESTIMATE_ID, side=Side.YES, size=100.0, at=AT, reviewer="alice")

    assert trade.status == TradeStatus.OPEN
    assert trade.entry_price == pytest.approx(
        fill_price(YES_PRICE, Side.YES, SPREAD, fee=0.0, slippage=0.0)
    )
    persisted = session.get(models.PaperTrade, uuid.UUID(trade.id))
    assert persisted is not None and persisted.status == TradeStatus.OPEN

    kinds = [event.kind for event in AuditRepository(session).list_for_market(MARKET_ID)]
    assert AuditKind.APPROVAL in kinds
    assert AuditKind.PAPER_OPEN in kinds


def test_reject_is_terminal_and_audited_without_a_trade(session: Session) -> None:
    _seed(session)
    _gate(session).reject(ESTIMATE_ID, reviewer="alice")

    kinds = [event.kind for event in AuditRepository(session).list_for_market(MARKET_ID)]
    assert kinds == [AuditKind.REJECTION]
    trades = session.exec(
        select(models.PaperTrade).where(models.PaperTrade.market_id == uuid.UUID(MARKET_ID))
    ).all()
    assert trades == []


def test_approve_vetoed_opportunity_raises_and_opens_nothing(session: Session) -> None:
    _seed(
        session,
        risk=RiskDecision(
            approved_size=0.0, vetoed=True, reasons=(RiskReason.CAP_PER_CATEGORY,), paper_only=True
        ),
    )
    with pytest.raises(VetoedOpportunityError):
        _gate(session).approve(ESTIMATE_ID, side=Side.YES, size=100.0, at=AT)

    trades = session.exec(
        select(models.PaperTrade).where(models.PaperTrade.market_id == uuid.UUID(MARKET_ID))
    ).all()
    assert trades == []


def test_approve_caps_size_at_the_risk_approved_size(session: Session) -> None:
    _seed(session, risk=_ok_decision(approved_size=40.0))
    trade = _gate(session).approve(ESTIMATE_ID, side=Side.YES, size=100.0, at=AT)
    assert trade.size == pytest.approx(40.0)


def test_approve_without_a_risk_decision_raises(session: Session) -> None:
    _seed(session)
    with pytest.raises(NotRiskEvaluatedError):
        _gate(session).approve(ESTIMATE_ID, side=Side.YES, size=100.0, at=AT)


def test_approve_outside_paper_mode_raises(session: Session) -> None:
    _seed(session)
    with pytest.raises(ExecutionModeError):
        _gate(session, mode="live").approve(ESTIMATE_ID, side=Side.YES, size=100.0, at=AT)


def test_approve_unscored_estimate_raises(session: Session) -> None:
    _seed(session, with_score=False)
    with pytest.raises(NotScoredError):
        _gate(session).approve(ESTIMATE_ID, side=Side.YES, size=100.0, at=AT)


def test_approve_unknown_estimate_raises(session: Session) -> None:
    with pytest.raises(UnknownEstimateError):
        _gate(session).approve(
            str(uuid.uuid5(uuid.NAMESPACE_URL, "missing")), side=Side.YES, size=100.0, at=AT
        )
