"""PYT-188 T5a: review gate copies the estimate's signal_source onto the opened PaperTrade."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from pythia.common import (
    AuditEvent,
    AuditKind,
    Estimate,
    Market,
    MarketSnapshot,
    Score,
    Side,
    SignalSource,
)
from pythia.config import Settings
from pythia.db.repositories import (
    AuditRepository,
    EstimateRepository,
    MarketRepository,
    ScoreRepository,
    SnapshotRepository,
)
from pythia.orchestration import ReviewGate
from pythia.paper import PaperLedger
from pythia.risk.models import RiskDecision

pytestmark = pytest.mark.integration

_AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_YES_PRICE = 0.6
_SPREAD = 0.04


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _ok_decision(approved_size: float = 1000.0) -> RiskDecision:
    return RiskDecision(approved_size=approved_size, vetoed=False, reasons=(), paper_only=True)


def _seed(
    session: Session,
    *,
    key: str,
    signal_source: SignalSource,
) -> str:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t5a-{key}-market"))
    snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t5a-{key}-snapshot"))
    estimate_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t5a-{key}-estimate"))

    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=f"pyt188-t5a-{key}",
            question="Will it resolve YES?",
            category="politics",
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=snapshot_id,
            market_id=market_id,
            ts=_AT,
            yes_price=_YES_PRICE,
            spread=_SPREAD,
            volume=1.0,
            liquidity=1_000.0,
        )
    )
    EstimateRepository(session).add(
        Estimate(
            id=estimate_id,
            market_id=market_id,
            snapshot_id=snapshot_id,
            p_model=0.7,
            confidence=0.8,
            lower=0.6,
            upper=0.8,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
            signal_source=signal_source,
        )
    )
    ScoreRepository(session).add(
        Score(
            estimate_id=estimate_id,
            edge=0.1,
            edge_net=0.08,
            ev_unit=0.08,
            p_resol=0.6,
            p_liq=1.0,
            p_time=1.0,
            p_corr=1.0,
            opportunity_score=0.0384,
            kelly_fraction=0.2,
            signal_source=signal_source,
        )
    )
    AuditRepository(session).append(
        AuditEvent(
            kind=AuditKind.RISK_DECISION,
            market_id=market_id,
            payload={
                "estimate_id": estimate_id,
                "approved_size": 1000.0,
                "vetoed": False,
                "paper_only": True,
                "reasons": [],
            },
        )
    )
    return estimate_id


def _gate(session: Session) -> ReviewGate:
    return ReviewGate(session, PaperLedger(session), _settings())


def test_approve_smartmoney_estimate_stamps_smartmoney_on_trade(session: Session) -> None:
    estimate_id = _seed(session, key="sm", signal_source=SignalSource.SMARTMONEY)
    trade = _gate(session).approve(estimate_id, side=Side.YES, size=100.0, at=_AT)
    assert trade.signal_source is SignalSource.SMARTMONEY


def test_approve_radar_estimate_stamps_radar_on_trade(session: Session) -> None:
    estimate_id = _seed(session, key="radar", signal_source=SignalSource.RADAR)
    trade = _gate(session).approve(estimate_id, side=Side.YES, size=100.0, at=_AT)
    assert trade.signal_source is SignalSource.RADAR
