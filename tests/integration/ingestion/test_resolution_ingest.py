"""Resolution-ingest tick on real Postgres (PYT-69): auto-record objective venue outcomes."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlmodel import Session

from pythia.common import Estimate, Market, MarketSnapshot, PaperTrade, Side, TradeStatus
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import (
    EstimateRepository,
    MarketRepository,
    ResolutionRepository,
    SnapshotRepository,
)
from pythia.ingestion import MarketCrawler
from pythia.paper import PaperLedger
from pythia.worker.resolution_ingest import ingest_tick

pytestmark = pytest.mark.integration

AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
EXTERNAL_ID = "0xpyt69resolved"


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


class _ResolvedTransport:
    def __init__(self, markets: list[dict[str, Any]]) -> None:
        self._markets = markets

    def fetch_markets(
        self,
        *,
        closed: bool = False,
        end_date_max: str | None = None,
        end_date_min: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._markets if closed else []

    def fetch_event_markets(self, slug: str) -> list[dict[str, Any]]:
        return []


def _resolved_raw(condition_id: str, yes_price: str) -> dict[str, Any]:
    no_price = "0" if yes_price == "1" else "1"
    return {
        "conditionId": condition_id,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": f'["{yes_price}", "{no_price}"]',
        "endDate": "2026-06-05T12:00:00Z",
        "closed": True,
    }


def _uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def _seed_tracked_market(session: Session) -> str:
    market_id = _uid(EXTERNAL_ID)
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=EXTERNAL_ID,
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    snapshot_id = _uid(f"{EXTERNAL_ID}-snap")
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
            id=_uid(f"{EXTERNAL_ID}-est"),
            market_id=market_id,
            snapshot_id=snapshot_id,
            p_model=0.7,
            confidence=0.8,
            lower=0.6,
            upper=0.8,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
        )
    )
    PaperLedger(session).open(
        PaperTrade(
            id=_uid(f"{EXTERNAL_ID}-trade"),
            market_id=market_id,
            estimate_id=_uid(f"{EXTERNAL_ID}-est"),
            side=Side.YES,
            entry_price=0.5,
            size=100.0,
            status=TradeStatus.OPEN,
            opened_at=AT,
        )
    )
    return market_id


def _crawler(markets: list[dict[str, Any]]) -> MarketCrawler:
    return MarketCrawler(_ResolvedTransport(markets), clock=lambda: AT, sleep=lambda _s: None)


def test_ingest_tick_records_and_settles_a_tracked_resolution(session: Session) -> None:
    market_id = _seed_tracked_market(session)

    recorded = ingest_tick(session, _settings(), _crawler([_resolved_raw(EXTERNAL_ID, "1")]))

    assert recorded == 1
    assert ResolutionRepository(session).exists(market_id)
    trade = session.get(models.PaperTrade, uuid.UUID(_uid(f"{EXTERNAL_ID}-trade")))
    assert trade is not None and trade.status == TradeStatus.RESOLVED and trade.pnl is not None


def test_ingest_tick_skips_untracked_and_already_resolved(session: Session) -> None:
    _seed_tracked_market(session)
    crawler = _crawler([_resolved_raw(EXTERNAL_ID, "1"), _resolved_raw("0xuntracked", "0")])

    assert ingest_tick(session, _settings(), crawler) == 1
    assert ingest_tick(session, _settings(), crawler) == 0
