"""Integration (PYT-143): event veto wiring — an open YES makes a 2nd YES on the event veto."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from pythia.common import (
    Estimate,
    Market,
    MarketSnapshot,
    PaperTrade,
    Side,
    SignalSource,
    TradeStatus,
)
from pythia.config import Settings
from pythia.db.repositories import EstimateRepository, MarketRepository, SnapshotRepository
from pythia.paper import PaperLedger
from pythia.risk import RiskLimits, RiskReason, TradeProposal, evaluate
from pythia.services.portfolio import PortfolioService

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

_LIMITS = RiskLimits(
    cap_per_trade=100.0,
    cap_per_market=100.0,
    cap_per_category=100.0,
    cap_daily=100.0,
    cap_weekly=100.0,
    cap_absolute=100.0,
    min_source_reliability=0.0,
    min_confidence=0.0,
    max_p_resol_severity=1.0,
    cap_per_event=100.0,
)


def _market_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def _seed_option(session: Session, name: str) -> str:
    market_id = _market_id(name)
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=f"0x{name}",
            question=f"Will {name} win?",
            category="politics",
            resolution_rule="r",
            event_slug="evt",
            neg_risk=True,
        )
    )
    snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"snap-{name}"))
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=snapshot_id,
            market_id=market_id,
            ts=_NOW,
            yes_price=0.4,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    estimate_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"est-{name}"))
    EstimateRepository(session).add(
        Estimate(
            id=estimate_id,
            market_id=market_id,
            snapshot_id=snapshot_id,
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
    return estimate_id


def test_open_yes_on_an_exclusive_event_vetoes_a_second_yes(session: Session) -> None:
    estimate_a = _seed_option(session, "team-a")
    _seed_option(session, "team-b")  # second option of the same negRisk event
    PaperLedger(session).open(
        PaperTrade(
            id=str(uuid.uuid4()),
            market_id=_market_id("team-a"),
            estimate_id=estimate_a,
            side=Side.YES,
            entry_price=0.4,
            size=10.0,
            status=TradeStatus.OPEN,
            opened_at=_NOW,
            signal_source=SignalSource.UNKNOWN,
        )
    )

    settings = Settings(_env_file=None, database_url="postgresql+psycopg://x/y")
    exposure = PortfolioService(session, settings).exposure_state()
    assert exposure.event_open_yes == {"evt": 1}
    assert exposure.per_event["evt"] == pytest.approx(10.0)

    # A second YES on the same event (as the pipeline would propose for option B) is vetoed.
    proposal = TradeProposal(
        proposed_size=5.0,
        category="politics",
        correlation_group=_market_id("team-b"),
        confidence=0.8,
        source_reliability=1.0,
        p_resol_severity=0.0,
        opportunity_score=0.5,
        category_calibrated=True,
        side=Side.YES,
        event_slug="evt",
        event_exclusive=True,
    )
    decision = evaluate(proposal, _LIMITS, exposure)
    assert decision.vetoed
    assert RiskReason.EVENT_DOUBLE_YES in decision.reasons
