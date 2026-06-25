"""Offline tests for the Falcon adapter (PYT-88): DTO parsing, fail-soft, auth, pagination."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from pythia.ingestion.falcon import (
    FalconClient,
    FalconPort,
    FixtureFalconTransport,
    HttpxFalconTransport,
    make_falcon_client,
)
from pythia.ingestion.falcon.client import FalconDataset
from pythia.ingestion.market_crawler import LiveVenueAccessError, TransientTransportError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "falcon"
ASOF = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _fixture_client() -> FalconClient:
    return FalconClient(FixtureFalconTransport(FIXTURES))


def test_top_traders_parse_and_coerce_string_numbers() -> None:
    traders = _fixture_client().list_top_traders()
    assert len(traders) == 3
    top = traders[0]
    assert top.address == "0x0720803c7cb0d0c5a928787b3b7ea148c6831cdb"
    assert top.rank == 1
    assert top.total_pnl == pytest.approx(1494121.7168)  # parsed from a JSON string
    assert top.sharpe_ratio is None  # null in the wire response


def test_top_traders_limit_caps_results() -> None:
    assert len(_fixture_client().list_top_traders(limit=2)) == 2


def test_trader_pnl_parses_the_daily_series_with_wallet_alias() -> None:
    points = _fixture_client().get_trader_pnl("0x8e433d", ASOF)
    assert len(points) == 2
    assert points[0].pnl == pytest.approx(-302209.22)
    assert points[0].wallet == "0x8e433d051bfb5cc6a1f7e5a0452029b7ffb4a3cd"  # proxy_wallet alias


def test_trader_trades_parse_with_field_aliases() -> None:
    trades = _fixture_client().get_trader_trades("0x4bfb41", ASOF)
    assert len(trades) == 2
    trade = trades[0]
    assert trade.trade_id.startswith("0x19320a9c")  # "id" alias
    assert trade.wallet == "0x11514fb5ef1e3fe110691243714fcf2182db1fed"  # proxy_wallet alias
    assert trade.side == "BUY"
    assert trade.price == pytest.approx(0.001)
    assert trade.ts == datetime(2026, 1, 9, 15, 51, tzinfo=UTC)  # timestamp alias


def test_market_returns_the_first_result() -> None:
    market = _fixture_client().get_market(
        "0xeaff81adbcd9dcd88a73a41402cb4a6b87234213a6b3ecaa0441126cde6d5b7f"
    )
    assert market is not None
    assert market.question == "Bitcoin Up or Down - January 17, 3PM ET"
    assert market.closed is True
    assert market.volume_total == pytest.approx(104456.760687)
    assert market.winning_outcome == "Up"


def test_orderbook_parses_levels_and_coerces_prices() -> None:
    book = _fixture_client().get_orderbook("69435545")
    assert book is not None
    assert book.token_id.startswith("694355")
    assert book.asks[0].price == pytest.approx(0.99) and book.asks[0].size == pytest.approx(60.0)
    assert len(book.bids) == 3


def test_falcon_client_conforms_to_port() -> None:
    assert isinstance(_fixture_client(), FalconPort)


def test_make_falcon_client_is_offline_without_a_token() -> None:
    # AC: unset token ⇒ offline fixtures, no crash, no live call.
    client = make_falcon_client(
        base_url="https://falcon.example", token=None, use_live=True, fixtures_dir=FIXTURES
    )
    assert len(client.list_top_traders()) == 3


class _RaisingTransport:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def retrieve(self, dataset: FalconDataset, params: dict[str, Any]) -> Any:
        raise self._exc


def test_client_is_fail_soft_when_falcon_is_down() -> None:
    client = FalconClient(_RaisingTransport(TransientTransportError("503")))
    assert client.list_top_traders() == []
    assert client.get_market("0xabc") is None
    assert client.get_orderbook("123") is None


def test_client_surfaces_a_geo_block_to_the_operator() -> None:
    client = FalconClient(_RaisingTransport(LiveVenueAccessError("enable VPN")))
    with pytest.raises(LiveVenueAccessError):
        client.list_top_traders()


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_httpx_transport_sends_bearer_agent_id_and_paginates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        {"data": {"results": [{"a": 1}]}, "pagination": {"has_more": True}},
        {"data": {"results": [{"a": 2}]}, "pagination": {"has_more": False}},
    ]
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, json: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
        calls.append({"url": url, "body": json, "headers": headers})
        return _FakeResponse(pages[json["pagination"]["offset"] // 100])

    monkeypatch.setattr(httpx, "post", fake_post)
    transport = HttpxFalconTransport(
        "https://falcon.example", "secret-token", {"top_traders": 579}, limit=100
    )
    out = transport.retrieve("top_traders", {"wallet_address": "ALL"})

    assert [c["body"]["pagination"]["offset"] for c in calls] == [0, 100]  # paginated
    assert out["data"]["results"] == [{"a": 1}, {"a": 2}]  # merged across pages
    assert calls[0]["headers"]["Authorization"] == "Bearer secret-token"
    assert calls[0]["body"]["agent_id"] == 579
    assert calls[0]["url"].endswith("/api/v2/semantic/retrieve/parameterized")


def test_httpx_transport_classifies_a_connection_error_as_geo_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(*_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", fake_post)
    transport = HttpxFalconTransport("https://falcon.example", "t", {"top_traders": 579})
    with pytest.raises(LiveVenueAccessError):
        transport.retrieve("top_traders", {})
