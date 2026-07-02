"""Startup reconciliation (PYT-175): settle offline resolutions; degrade on venue error."""

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlmodel import Session

from pythia.common import Estimate, Market, MarketSnapshot, PaperTrade, Side, TradeStatus
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import EstimateRepository, MarketRepository, SnapshotRepository
from pythia.ingestion import MarketCrawler
from pythia.ingestion.market_crawler import LiveVenueAccessError
from pythia.paper import PaperLedger
from pythia.worker.reconcile import reconcile_on_startup, settle_stale_positions

pytestmark = pytest.mark.integration

AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
EXTERNAL_ID = "0xpyt175resolved"


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


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


class _BoomTransport:
    def fetch_markets(self, **_: Any) -> list[dict[str, Any]]:
        raise LiveVenueAccessError("geo-blocked; enable the VPN (§8.6)")

    def fetch_event_markets(self, slug: str) -> list[dict[str, Any]]:
        return []


def _resolved_raw(condition_id: str) -> dict[str, Any]:
    return {
        "conditionId": condition_id,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["1", "0"]',
        "endDate": "2026-06-05T12:00:00Z",
        "closed": True,
    }


def _crawler(transport: Any) -> MarketCrawler:
    return MarketCrawler(transport, clock=lambda: AT, sleep=lambda _s: None)


def _seed_open_trade(session: Session) -> str:
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
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=_uid(f"{EXTERNAL_ID}-snap"),
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
            snapshot_id=_uid(f"{EXTERNAL_ID}-snap"),
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


def test_reconcile_settles_open_positions_on_startup(session: Session) -> None:
    _seed_open_trade(session)
    reconcile_on_startup(
        lambda: nullcontext(session),
        _settings(),
        _crawler(_ResolvedTransport([_resolved_raw(EXTERNAL_ID)])),
    )
    trade = session.get(models.PaperTrade, uuid.UUID(_uid(f"{EXTERNAL_ID}-trade")))
    assert trade is not None
    assert (
        trade.status == TradeStatus.RESOLVED and trade.pnl is not None
    )  # settled before discovery


class _Fetcher:
    def __init__(self, raw: dict[str, Any] | None) -> None:
        self._raw = raw

    def fetch_market_by_condition(self, condition_id: str) -> dict[str, Any] | None:
        return self._raw


def _mark_closed(session: Session, market_id: str) -> None:
    market = session.get(models.Market, uuid.UUID(market_id))
    assert market is not None
    market.close_time = AT  # trading closed in the past, market not marked resolved
    session.add(market)
    session.commit()


def test_settle_stale_positions_settles_a_closed_market_resolved_on_gamma(session: Session) -> None:
    market_id = _seed_open_trade(session)
    _mark_closed(session, market_id)  # closed for trading but resolution-ingest never caught it

    settled = settle_stale_positions(
        session, _settings(), _Fetcher(_resolved_raw(EXTERNAL_ID)), now=AT + timedelta(days=10)
    )

    assert settled == 1
    trade = session.get(models.PaperTrade, uuid.UUID(_uid(f"{EXTERNAL_ID}-trade")))
    assert trade is not None
    assert trade.status == TradeStatus.RESOLVED and trade.pnl is not None


def test_settle_stale_leaves_a_closed_but_unresolved_market_open(session: Session) -> None:
    market_id = _seed_open_trade(session)
    _mark_closed(session, market_id)
    raw = _resolved_raw(EXTERNAL_ID)
    raw["outcomePrices"] = '["0.5", "0.5"]'  # closed but not cleanly resolved (ambiguous)

    settled = settle_stale_positions(
        session, _settings(), _Fetcher(raw), now=AT + timedelta(days=10)
    )

    assert settled == 0
    trade = session.get(models.PaperTrade, uuid.UUID(_uid(f"{EXTERNAL_ID}-trade")))
    assert trade is not None and trade.status == TradeStatus.OPEN  # left open, no invented result


def test_reconcile_degrades_when_the_venue_is_unreachable(session: Session) -> None:
    _seed_open_trade(session)
    # A geo-block must not crash the worker — it logs and starts; the interval job retries (§8.6).
    reconcile_on_startup(lambda: nullcontext(session), _settings(), _crawler(_BoomTransport()))
    trade = session.get(models.PaperTrade, uuid.UUID(_uid(f"{EXTERNAL_ID}-trade")))
    assert trade is not None and trade.status == TradeStatus.OPEN  # untouched, no crash
