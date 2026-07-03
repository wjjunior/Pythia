"""Integration test (PYT-113): the worker recalibration-fit tick fits + stores a versioned map."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.common import Estimate, Market, MarketSnapshot, SignalSource
from pythia.config import Settings
from pythia.db.repositories import (
    EstimateRepository,
    MarketRepository,
    RecalibrationRepository,
    ResolutionRepository,
    SnapshotRepository,
)
from pythia.worker.scheduler import recalibration_tick

pytestmark = pytest.mark.integration

_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_ASOF = _TS + timedelta(days=30)


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y", recalibration_min_n=2)


def _uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def _resolved(session: Session, name: str, *, p_model: float, outcome: int) -> None:
    market = MarketRepository(session).upsert(
        Market(
            id=_uid(name), external_id=name, question="q", category="politics", resolution_rule="r"
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=_uid(f"{name}-snap"),
            market_id=market.id,
            ts=_TS,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    EstimateRepository(session).add(
        Estimate(
            id=_uid(f"{name}-est"),
            market_id=market.id,
            snapshot_id=_uid(f"{name}-snap"),
            p_model=p_model,
            confidence=0.6,
            lower=0.0,
            upper=1.0,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
            signal_source=SignalSource.UNKNOWN,
        )
    )
    ResolutionRepository(session).record(market.id, outcome, at=_TS + timedelta(days=1))


def test_should_fit_and_store_a_point_in_time_map_on_the_recalibration_tick(
    session: Session,
) -> None:
    _resolved(session, "r1", p_model=0.8, outcome=1)
    _resolved(session, "r2", p_model=0.3, outcome=0)

    @contextmanager
    def _factory() -> Iterator[Session]:
        yield session

    recalibration_tick(_factory, _settings(), lambda: _ASOF)

    loaded = RecalibrationRepository(session).active_for_category("politics")
    assert loaded is not None
    cmap, _n_fit, fit_asof = loaded
    assert cmap.method == "platt"  # n=2 >= min_n=2 → a real parametric map, not identity
    assert fit_asof == _ASOF  # the logical asof the tick fit on (PYT-111), not wall-clock
