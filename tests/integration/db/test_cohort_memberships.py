"""Integration tests for cohort_memberships (PYT-95): append-only, idempotent tagging."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from pythia.common import Estimate, Market, MarketSnapshot, SignalSource
from pythia.db import models
from pythia.db.repositories import (
    CohortMembershipRepo,
    EstimateRepository,
    MarketRepository,
    SnapshotRepository,
)
from pythia.eval.cohorts import Cohort

pytestmark = pytest.mark.integration
_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

_MKT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "cm-mkt"))
_SNAP_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "cm-snap"))
_EST_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "cm-est"))


def _trade_id(session: Session) -> str:
    MarketRepository(session).upsert(
        Market(
            id=_MKT_ID,
            external_id="cm",
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=_SNAP_ID,
            market_id=_MKT_ID,
            ts=_TS,
            yes_price=0.5,
            spread=0.02,
            volume=1000.0,
            liquidity=5000.0,
        )
    )
    EstimateRepository(session).add(
        Estimate(
            id=_EST_ID,
            market_id=_MKT_ID,
            snapshot_id=_SNAP_ID,
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
    tid = str(uuid.uuid5(uuid.NAMESPACE_URL, "cm-trade"))
    session.add(
        models.PaperTrade(
            id=uuid.UUID(tid),
            market_id=uuid.UUID(_MKT_ID),
            estimate_id=uuid.UUID(_EST_ID),
            side="YES",
            entry_price=0.5,
            size=10.0,
            status="open",
            opened_at=_TS,
        )
    )
    session.commit()
    return tid


def test_add_many_tags_and_is_idempotent(session: Session) -> None:
    tid = _trade_id(session)
    repo = CohortMembershipRepo(session)
    repo.add_many(tid, [Cohort.FAST_7D, Cohort.MIDBAND], asof=_TS)
    repo.add_many(tid, [Cohort.FAST_7D], asof=_TS)  # re-tag → no duplicate
    assert repo.cohorts_of(tid) == {Cohort.FAST_7D, Cohort.MIDBAND}
