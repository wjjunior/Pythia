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
from pythia.ingestion.falcon.client import (
    _FALCON_AGENT_IDS,
    FalconDataset,
    FalconDatasetUnconfigured,
    _merge_agent_ids,
)
from pythia.ingestion.market_crawler import LiveVenueAccessError, TransientTransportError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "falcon"
ASOF = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _fixture_client() -> FalconClient:
    return FalconClient(FixtureFalconTransport(FIXTURES))


def test_top_traders_parse_and_coerce_string_numbers() -> None:
    traders = _fixture_client().list_top_traders()
    assert len(traders) == 3
    top = traders[0]
    assert top.address == "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563"
    assert top.rank == 1
    assert top.total_pnl == pytest.approx(1617522.5693)  # parsed from a JSON string
    assert top.total_invested == pytest.approx(18400914.27)
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


def test_open_positions_parse_and_coerce_string_numbers() -> None:
    positions = _fixture_client().get_open_positions("0x11514fb5")
    assert len(positions) == 2
    assert positions[0].condition_id.startswith("0x584654")
    assert positions[0].size == pytest.approx(1200.5)  # JSON string coerced
    assert positions[0].avg_price == pytest.approx(0.62)  # historical price parsed, never evaluated


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


def test_client_is_fail_soft_when_an_agent_id_is_unconfigured() -> None:
    # Live transport knows only the top-traders agent_id; other datasets must fail soft (no data),
    # never KeyError-crash. The guard precedes any POST, so this makes no network call.
    transport = HttpxFalconTransport(
        "https://falcon.example/api/v2/semantic/retrieve/parameterized", "t", {"top_traders": 579}
    )
    client = FalconClient(transport)
    assert client.get_market("0xabc") is None
    assert client.get_trader_pnl("0xabc", ASOF) == []


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
    endpoint = "https://falcon.example/api/v2/semantic/retrieve/parameterized"
    transport = HttpxFalconTransport(
        endpoint, "secret-token", {"top_traders": 579}, use_live=True, limit=100
    )
    out = transport.retrieve("top_traders", {"wallet_address": "ALL"})

    assert [c["body"]["pagination"]["offset"] for c in calls] == [0, 100]  # paginated
    assert out["data"]["results"] == [{"a": 1}, {"a": 2}]  # merged across pages
    assert calls[0]["headers"]["Authorization"] == "Bearer secret-token"
    assert calls[0]["body"]["agent_id"] == 579
    assert calls[0]["url"] == endpoint  # FALCON_API_BASE is used as-is, no path appended


def test_httpx_transport_classifies_a_connection_error_as_geo_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(*_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", fake_post)
    transport = HttpxFalconTransport(
        "https://falcon.example", "t", {"top_traders": 579}, use_live=True
    )
    with pytest.raises(LiveVenueAccessError):
        transport.retrieve("top_traders", {})


def test_httpx_transport_refuses_a_live_call_when_use_live_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PYT-120/§8.6 defense-in-depth: the transport refuses BEFORE any POST, independent of the
    # factory/entrypoint gate — httpx.post must never run with the flag off.
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("live Falcon POST made with USE_LIVE_VENUES off")

    monkeypatch.setattr(httpx, "post", _boom)
    transport = HttpxFalconTransport("https://falcon.example", "t", {"top_traders": 579})
    with pytest.raises(LiveVenueAccessError):
        transport.retrieve("top_traders", {})


def test_default_agent_ids_are_the_confirmed_catalog() -> None:
    # PYT-131: the confirmed live ids. orderbook (572) is held until get_orderbook sends the
    # required time-range; open_positions has no Falcon endpoint (derived from trades in PYT-132).
    assert _FALCON_AGENT_IDS == {
        "top_traders": 579,
        "trader_trades": 556,
        "market": 574,
        "trader_pnl": 569,
    }


def test_merge_agent_ids_empty_returns_the_defaults() -> None:
    assert _merge_agent_ids({}) == _FALCON_AGENT_IDS


def test_merge_agent_ids_patches_a_single_id_over_the_defaults() -> None:
    merged = _merge_agent_ids({"trader_trades": 999})
    assert merged["trader_trades"] == 999  # operator override wins
    assert merged["market"] == 574 and merged["top_traders"] == 579  # other defaults intact


def test_merge_agent_ids_rejects_an_unknown_dataset() -> None:
    with pytest.raises(ValueError, match="unknown Falcon dataset"):
        _merge_agent_ids({"not_a_dataset": 1})


def test_unconfigured_dataset_raises_a_distinct_typed_error_not_transient() -> None:
    # A missing agent_id is a permanent capability gap, not a Falcon-down blip — so it must NOT be a
    # TransientTransportError (which logs at warning every call and floods the worker).
    transport = HttpxFalconTransport(
        "https://falcon.example/api/v2/semantic/retrieve/parameterized", "t", {"top_traders": 579}
    )
    with pytest.raises(FalconDatasetUnconfigured):
        transport.retrieve("open_positions", {})
    assert not issubclass(FalconDatasetUnconfigured, TransientTransportError)


def test_client_is_fail_soft_on_an_unconfigured_dataset() -> None:
    client = FalconClient(_RaisingTransport(FalconDatasetUnconfigured("open_positions")))
    assert client.get_open_positions("0xabc") == []
    assert client.get_market("0xabc") is None
