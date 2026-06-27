"""Integration (PYT-141): /api/arbs surfaces persisted structural-arbitrage baskets."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from pythia.api.app import create_app
from pythia.api.deps import get_session, settings_dependency
from pythia.common import ArbKind, ArbLeg, ArbOpportunity, Side
from pythia.config import Settings
from pythia.db.repositories import ArbRepository

pytestmark = pytest.mark.integration


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
