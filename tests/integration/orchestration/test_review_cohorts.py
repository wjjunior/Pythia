"""Integration tests (PYT-95): the review gate tags cohorts and honours the optional filter.

Seeds reuse the same helpers as test_review.py: market + snapshot + estimate + score +
risk-decision audit event. All three tests use real Postgres via the shared `session` fixture.
"""

import uuid
from datetime import UTC, datetime, timedelta

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
)
from pythia.config import Settings
from pythia.db.repositories import (
    AuditRepository,
    CohortMembershipRepo,
    EstimateRepository,
    MarketRepository,
    ScoreRepository,
    SnapshotRepository,
)
from pythia.eval.cohorts import Cohort
from pythia.orchestration import OutOfCohortError, ReviewGate
from pythia.paper import PaperLedger
from pythia.risk.models import RiskDecision

pytestmark = pytest.mark.integration

# ── shared constants ─────────────────────────────────────────────────────────
SNAPSHOT_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
YES_PRICE = 0.5
SPREAD = 0.02


def _market_id(tag: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt95-market-{tag}"))


def _snapshot_id(tag: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt95-snapshot-{tag}"))


def _estimate_id(tag: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt95-estimate-{tag}"))


def _ok_decision(approved_size: float = 1000.0) -> RiskDecision:
    return RiskDecision(approved_size=approved_size, vetoed=False, reasons=(), paper_only=True)


def _seed(
    session: Session,
    *,
    tag: str,
    close_time: datetime,
) -> tuple[str, str, str]:
    """Insert market + snapshot + estimate + score + risk-decision."""
    market_id = _market_id(tag)
    snapshot_id = _snapshot_id(tag)
    estimate_id = _estimate_id(tag)

    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=f"pyt95-{tag}",
            question="Will it resolve YES?",
            category="politics",
            resolution_rule="rule",
            close_time=close_time,
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=snapshot_id,
            market_id=market_id,
            ts=SNAPSHOT_TS,
            yes_price=YES_PRICE,
            spread=SPREAD,
            volume=1.0,
            liquidity=1.0,
        )
    )
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
        )
    )
    risk = _ok_decision()
    AuditRepository(session).append(
        AuditEvent(
            kind=AuditKind.RISK_DECISION,
            market_id=market_id,
            payload={
                "estimate_id": estimate_id,
                "approved_size": risk.approved_size,
                "vetoed": risk.vetoed,
                "paper_only": risk.paper_only,
                "reasons": [reason.value for reason in risk.reasons],
            },
        )
    )
    return market_id, snapshot_id, estimate_id


def _settings(
    *,
    database_url: str = "postgresql+psycopg://x/y",
    cohort_trade_filter_enabled: bool = False,
    cohort_trade_filter: Cohort = Cohort.FAST_7D,
) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        cohort_trade_filter_enabled=cohort_trade_filter_enabled,
        cohort_trade_filter=cohort_trade_filter,
    )


def _gate(session: Session, *, settings: Settings) -> ReviewGate:
    return ReviewGate(session, PaperLedger(session), settings)


# ── tests ─────────────────────────────────────────────────────────────────────


def test_approve_tags_point_in_time_cohorts(session: Session) -> None:
    """Filter OFF: approve succeeds; trade is tagged {FAST_7D, MIDBAND} (3-day, 0.5 price)."""
    close_time = SNAPSHOT_TS + timedelta(days=3)
    _market_id_val, _snap_id, estimate_id = _seed(session, tag="tag3d", close_time=close_time)

    settings = _settings(cohort_trade_filter_enabled=False)
    trade = _gate(session, settings=settings).approve(
        estimate_id, side=Side.YES, size=100.0, at=SNAPSHOT_TS
    )

    cohorts = CohortMembershipRepo(session).cohorts_of(trade.id)
    assert cohorts == {Cohort.FAST_7D, Cohort.MIDBAND}


def test_filter_rejects_out_of_cohort_when_enabled(session: Session) -> None:
    """Filter ON (fast_7d) and market closes in 30 days → OutOfCohortError; no trade opened."""
    close_time = SNAPSHOT_TS + timedelta(days=30)
    market_id, _snap_id, estimate_id = _seed(session, tag="tag30d", close_time=close_time)

    settings = _settings(
        cohort_trade_filter_enabled=True,
        cohort_trade_filter=Cohort.FAST_7D,
    )
    with pytest.raises(OutOfCohortError):
        _gate(session, settings=settings).approve(
            estimate_id, side=Side.YES, size=100.0, at=SNAPSHOT_TS
        )

    # No paper trade must have been created
    from sqlmodel import select

    from pythia.db import models

    trades = session.exec(
        select(models.PaperTrade).where(models.PaperTrade.market_id == uuid.UUID(market_id))
    ).all()
    assert trades == []


def test_filter_off_is_unchanged(session: Session) -> None:
    """Same 30-day market, filter DISABLED → approve succeeds (no behaviour change)."""
    close_time = SNAPSHOT_TS + timedelta(days=30)
    market_id, _snap_id, estimate_id = _seed(session, tag="tag30d-off", close_time=close_time)

    settings = _settings(cohort_trade_filter_enabled=False)
    trade = _gate(session, settings=settings).approve(
        estimate_id, side=Side.YES, size=100.0, at=SNAPSHOT_TS
    )

    # Assert exactly one PaperTrade row was created for this market
    from sqlmodel import select

    from pythia.db import models

    trades = session.exec(
        select(models.PaperTrade).where(models.PaperTrade.market_id == uuid.UUID(market_id))
    ).all()
    assert len(trades) == 1
    cohorts = CohortMembershipRepo(session).cohorts_of(trade.id)
    # 30-day horizon: not fast_7d; price 0.5 is midband → only MIDBAND
    assert cohorts == {Cohort.MIDBAND}
