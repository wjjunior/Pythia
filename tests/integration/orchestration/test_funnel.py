"""Integration tests (PYT-164): the per-tick funnel is emitted, persisted, reconciles, additive."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session, select

from pythia.common import Market, MarketSnapshot, PipelineFunnel
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import MarketRepository, ScoreRepository, SnapshotRepository
from pythia.orchestration.runner import make_funnel_sink, run_open_markets

pytestmark = pytest.mark.integration

_ASOF = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _seed(session: Session, slug: str, *, category: str = "politics") -> None:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, slug))
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=slug,
            question="q?",
            category=category,
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}-snap")),
            market_id=market_id,
            ts=_ASOF,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )


def test_run_emits_a_reconciling_funnel(session: Session) -> None:
    _seed(session, "f1", category="politics")
    _seed(session, "f2", category="sports")
    captured: list[PipelineFunnel] = []

    run_open_markets(session, _settings(), funnel_sink=captured.append)

    assert len(captured) == 1
    funnel = captured[0]
    assert funnel.reconciles
    assert funnel.crawled == 2 and funnel.tracked_open == 2
    assert funnel.scored == 2  # both open markets scored this tick
    assert funnel.categories == {"politics": 1, "sports": 1}  # per-category crawl representation


def test_make_funnel_sink_persists_a_funnel_row(session: Session) -> None:
    _seed(session, "f3")

    run_open_markets(session, _settings(), funnel_sink=make_funnel_sink(session))

    rows = session.exec(select(models.PipelineRunFunnel)).all()
    assert len(rows) == 1
    assert rows[0].crawled == 1 and rows[0].scored == 1


def test_funnel_does_not_change_radar_output(session: Session) -> None:
    _seed(session, "f4")
    asof = datetime.now(UTC)
    run_open_markets(session, _settings(), funnel_sink=make_funnel_sink(session))
    radar_before = ScoreRepository(session).radar(asof=asof)

    # A second tick re-emits a funnel but estimates nothing new (PYT-73) — the radar must not move.
    run_open_markets(session, _settings(), funnel_sink=make_funnel_sink(session))
    radar_after = ScoreRepository(session).radar(asof=asof)

    assert radar_before == radar_after  # the funnel is purely additive
    assert len(session.exec(select(models.PipelineRunFunnel)).all()) == 2
