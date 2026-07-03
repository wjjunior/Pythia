"""Resolution backfill (PYT-187): record Gamma resolutions for estimated markets that ended but
the window-bounded crawl never caught. Real Postgres."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlmodel import Session

from pythia.common import Estimate, Market, MarketSnapshot, SignalSource
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import (
    EstimateRepository,
    MarketRepository,
    ResolutionRepository,
    SnapshotRepository,
)
from pythia.worker.resolution_ingest import backfill_resolutions

pytestmark = pytest.mark.integration

AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def _resolved_raw(condition_id: str, yes_price: str) -> dict[str, Any]:
    no_price = "0" if yes_price == "1" else "1"
    return {
        "conditionId": condition_id,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": f'["{yes_price}", "{no_price}"]',
        "endDate": "2026-06-05T12:00:00Z",
        "closed": True,
    }


class _Fetcher:
    def __init__(self, by_condition: dict[str, dict[str, Any] | None]) -> None:
        self._by = by_condition
        self.include_closed_calls: list[bool] = []

    def fetch_market_by_condition(
        self, condition_id: str, *, include_closed: bool = False
    ) -> dict[str, Any] | None:
        self.include_closed_calls.append(include_closed)
        return self._by.get(condition_id)


def _seed_estimated_market(
    session: Session, external_id: str, *, close_time: datetime | None, category: str = "weather"
) -> str:
    market_id = _uid(external_id)
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=external_id,
            question="q",
            category=category,
            resolution_rule="r",
            close_time=close_time,
        )
    )
    snapshot_id = _uid(f"{external_id}-snap")
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
    EstimateRepository(session).add(
        Estimate(
            id=_uid(f"{external_id}-est"),
            market_id=market_id,
            snapshot_id=snapshot_id,
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
    return market_id


def test_backfill_records_an_estimated_ended_unresolved_market(session: Session) -> None:
    ext = "0xpyt187a"
    market_id = _seed_estimated_market(session, ext, close_time=AT - timedelta(days=1))
    fetcher = _Fetcher({ext: _resolved_raw(ext, "1")})

    recorded = backfill_resolutions(session, _settings(), fetcher, now=AT)

    assert recorded == 1
    assert fetcher.include_closed_calls == [True]  # PYT-182: a closed market needs closed=true
    assert ResolutionRepository(session).exists(market_id)
    market = session.get(models.Market, uuid.UUID(market_id))
    assert market is not None and market.resolved is True


def test_backfill_leaves_a_closed_but_unresolved_market(session: Session) -> None:
    ext = "0xpyt187b"
    market_id = _seed_estimated_market(session, ext, close_time=AT - timedelta(days=1))
    raw = _resolved_raw(ext, "1")
    raw["outcomePrices"] = '["0.5", "0.5"]'  # closed but not cleanly resolved (ambiguous prices)

    recorded = backfill_resolutions(session, _settings(), _Fetcher({ext: raw}), now=AT)

    assert recorded == 0
    assert not ResolutionRepository(session).exists(market_id)  # left open, no invented result


def test_backfill_ignores_a_market_that_has_not_ended(session: Session) -> None:
    ext = "0xpyt187c"
    _seed_estimated_market(session, ext, close_time=AT + timedelta(days=1))  # closes in the future
    fetcher = _Fetcher({ext: _resolved_raw(ext, "1")})

    assert backfill_resolutions(session, _settings(), fetcher, now=AT) == 0
    assert fetcher.include_closed_calls == []  # never even fetched — not a candidate


def test_backfill_ignores_a_market_without_an_estimate(session: Session) -> None:
    ext = "0xpyt187d"
    MarketRepository(session).upsert(
        Market(
            id=_uid(ext),
            external_id=ext,
            question="q",
            category="weather",
            resolution_rule="r",
            close_time=AT - timedelta(days=1),
        )
    )
    assert (
        backfill_resolutions(session, _settings(), _Fetcher({ext: _resolved_raw(ext, "1")}), now=AT)
        == 0
    )


def test_backfill_respects_the_limit_and_drains_across_ticks(session: Session) -> None:
    a = _seed_estimated_market(session, "0xpyt187e1", close_time=AT - timedelta(days=2))
    b = _seed_estimated_market(session, "0xpyt187e2", close_time=AT - timedelta(days=1))
    fetcher = _Fetcher(
        {
            "0xpyt187e1": _resolved_raw("0xpyt187e1", "1"),
            "0xpyt187e2": _resolved_raw("0xpyt187e2", "0"),
        }
    )

    first = backfill_resolutions(session, _settings(), fetcher, now=AT, limit=1)
    assert first == 1
    assert ResolutionRepository(session).exists(a)  # oldest close_time first
    assert not ResolutionRepository(session).exists(b)

    second = backfill_resolutions(session, _settings(), fetcher, now=AT, limit=1)
    assert second == 1
    assert ResolutionRepository(session).exists(b)
