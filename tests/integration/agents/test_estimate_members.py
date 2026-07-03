"""estimate_members persistence against real Postgres (§4.7, PYT-32)."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from pythia.common import Estimate, Market, MarketSnapshot, MemberForecast, SignalSource
from pythia.db.repositories import (
    EstimateMemberRepository,
    EstimateRepository,
    MarketRepository,
    SnapshotRepository,
)

pytestmark = pytest.mark.integration

MARKET_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt32-market"))
SNAPSHOT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt32-snapshot"))
ESTIMATE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt32-estimate"))
TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _seed(session: Session) -> None:
    MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt32",
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=SNAPSHOT_ID,
            market_id=MARKET_ID,
            ts=TS,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    EstimateRepository(session).add(
        Estimate(
            id=ESTIMATE_ID,
            market_id=MARKET_ID,
            snapshot_id=SNAPSHOT_ID,
            p_model=0.6,
            confidence=0.5,
            lower=0.4,
            upper=0.7,
            rationale="r",
            evidence_ids=[],
            model_name="ensemble",
            signal_source=SignalSource.UNKNOWN,
        )
    )


def test_add_many_persists_and_lists_members(session: Session) -> None:
    _seed(session)
    members = [
        MemberForecast(model="a/m1", p_member=0.4),
        MemberForecast(model="b/m2", p_member=0.8, weight=2.0),
    ]
    EstimateMemberRepository(session).add_many(ESTIMATE_ID, members)

    stored = EstimateMemberRepository(session).list_for_estimate(ESTIMATE_ID)
    by_model = {member.model: member for member in stored}
    assert set(by_model) == {"a/m1", "b/m2"}
    assert by_model["b/m2"].p_member == 0.8
    assert by_model["b/m2"].weight == 2.0


def test_add_many_with_no_members_is_a_noop(session: Session) -> None:
    _seed(session)
    EstimateMemberRepository(session).add_many(ESTIMATE_ID, [])
    assert EstimateMemberRepository(session).list_for_estimate(ESTIMATE_ID) == []
