"""Integration (PYT-141, PYT-145): /api/arbs surfaces baskets and approves them to paper."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from pythia.api.app import create_app
from pythia.api.deps import get_session, settings_dependency
from pythia.common import ArbKind, ArbLeg, ArbOpportunity, Market, MarketSnapshot, Side
from pythia.config import Settings
from pythia.db.repositories import (
    ArbRepository,
    MarketRepository,
    PaperTradeRepository,
    SnapshotRepository,
)
from pythia.orchestration.runner import run_event_pass

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _seed_dutch_book(session: Session) -> None:
    for name in ("a", "b", "c"):  # three exclusive options at 0.3 ⇒ a dutch book
        market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"cup-{name}"))
        MarketRepository(session).upsert(
            Market(
                id=market_id,
                external_id=f"0xcup-{name}",
                question=f"Will {name} win?",
                category="politics",
                resolution_rule="r",
                event_slug="cup",
                neg_risk=True,
            )
        )
        SnapshotRepository(session).append(
            MarketSnapshot(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"snap-cup-{name}")),
                market_id=market_id,
                ts=_NOW,
                yes_price=0.3,
                spread=0.0,
                volume=1.0,
                liquidity=1.0,
            )
        )
    run_event_pass(session, now=_NOW)


@pytest.fixture
def app(session: Session) -> FastAPI:
    application = create_app()
    application.dependency_overrides[get_session] = lambda: session
    application.dependency_overrides[settings_dependency] = lambda: Settings(
        _env_file=None, database_url="postgresql+psycopg://x/y"
    )
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_arbs_endpoint_lists_persisted_baskets_by_ev(client: TestClient, session: Session) -> None:
    small = ArbOpportunity(
        event_slug="e1",
        kind=ArbKind.DUTCH_BOOK,
        legs=[ArbLeg(market_id="m1", side=Side.YES, price=0.3)],
        ev=0.05,
    )
    big = ArbOpportunity(
        event_slug="e2",
        kind=ArbKind.LADDER_SPREAD,
        legs=[
            ArbLeg(market_id="lo", side=Side.YES, price=0.6),
            ArbLeg(market_id="hi", side=Side.NO, price=0.3),
        ],
        ev=0.2,
    )
    repo = ArbRepository(session)
    repo.replace_for_event("e1", [small], detected_at=datetime.now(UTC))
    repo.replace_for_event("e2", [big], detected_at=datetime.now(UTC))

    body = client.get("/api/arbs").json()

    assert [item["event_slug"] for item in body] == ["e2", "e1"]  # ranked by EV desc
    assert body[0]["kind"] == "ladder_spread"
    assert body[0]["legs"][1] == {"market_id": "hi", "side": "NO", "price": 0.3}


def test_arbs_endpoint_is_empty_with_no_baskets(client: TestClient) -> None:
    assert client.get("/api/arbs").json() == []


def test_approve_opens_all_basket_legs(client: TestClient, session: Session) -> None:
    _seed_dutch_book(session)
    arb_id = client.get("/api/arbs").json()[0]["id"]
    assert arb_id  # the panel surfaces the persisted row id

    response = client.post(f"/api/arbs/{arb_id}/approve", json={"baskets": 10.0})

    assert response.status_code == 200
    body = response.json()
    assert body["arb_id"] == arb_id
    assert len(body["trades"]) == 3
    assert all(trade["estimate_id"] is None for trade in body["trades"])  # market-anchored
    assert len(PaperTradeRepository(session).list_open()) == 3  # all legs opened atomically


def test_approve_unknown_arb_is_404(client: TestClient) -> None:
    response = client.post(f"/api/arbs/{uuid.uuid4()}/approve", json={"baskets": 10.0})
    assert response.status_code == 404


def test_approve_rejects_a_non_positive_size(client: TestClient, session: Session) -> None:
    _seed_dutch_book(session)
    arb_id = client.get("/api/arbs").json()[0]["id"]
    response = client.post(f"/api/arbs/{arb_id}/approve", json={"baskets": 0.0})
    assert response.status_code == 422
