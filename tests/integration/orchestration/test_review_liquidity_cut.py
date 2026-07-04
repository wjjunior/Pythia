"""Integration test: review-gate fill is capped to snapshot liquidity (PYT-189 Task 5)."""

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

pytestmark = pytest.mark.integration

MARKET_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt189-t5-market"))
SNAPSHOT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt189-t5-snapshot"))
ESTIMATE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt189-t5-estimate"))
AT = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
YES_PRICE = 0.55
SPREAD = 0.02
LIQUIDITY = 5.0
SIZE = 20.0


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y", mode="paper")


def _seed(session: Session) -> None:
    MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt189-t5",
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
            volume=100.0,
            liquidity=LIQUIDITY,
        )
    )
    EstimateRepository(session).add(
        Estimate(
            id=ESTIMATE_ID,
            market_id=MARKET_ID,
            snapshot_id=SNAPSHOT_ID,
            p_model=0.65,
            confidence=0.8,
            lower=0.55,
            upper=0.75,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
            signal_source=SignalSource.UNKNOWN,
        )
    )
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
    AuditRepository(session).append(
        AuditEvent(
            kind=AuditKind.RISK_DECISION,
            market_id=MARKET_ID,
            payload={
                "estimate_id": ESTIMATE_ID,
                "approved_size": SIZE,
                "vetoed": False,
                "paper_only": True,
                "reasons": [],
            },
        )
    )


def test_approve_cuts_fill_to_snapshot_liquidity(session: Session) -> None:
    """Fill is capped to snapshot liquidity; cut reason is recorded in the APPROVAL audit event."""
    _seed(session)
    gate = ReviewGate(session, PaperLedger(session), _settings())
    trade = gate.approve(ESTIMATE_ID, side=Side.YES, size=SIZE, at=AT, reviewer="alice")

    assert trade.size == pytest.approx(LIQUIDITY)

    events = AuditRepository(session).list_for_market(MARKET_ID)
    approval_events = [e for e in events if e.kind == AuditKind.APPROVAL]
    assert len(approval_events) == 1
    payload = approval_events[0].payload
    assert payload["size"] == pytest.approx(LIQUIDITY)
    assert payload.get("liquidity_reason") == "size_exceeds_liquidity"
