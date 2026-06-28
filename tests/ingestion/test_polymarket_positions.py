"""Tests for the Polymarket data-api positions adapter: parse, fail-soft, VPN gating (offline)."""

from pathlib import Path
from typing import Any

import httpx
import pytest

from pythia.ingestion.market_crawler import LiveVenueAccessError, TransientTransportError
from pythia.ingestion.polymarket_positions import (
    HttpxPositionsTransport,
    PolymarketPositionsClient,
    make_positions_client,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "polymarket" / "positions.json"


class _FakeResponse:
    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict[str, Any]]:
        return self._payload


class _RaisingTransport:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def fetch_positions(self, wallet: str) -> list[dict[str, Any]]:
        raise self._exc


def test_client_parses_fixture_rows_into_held_positions() -> None:
    client = make_positions_client(base_url="x", use_live=False, fixtures_path=FIXTURE)

    held = client.current_positions("0xwhale")

    assert [p.condition_id for p in held] == ["0xcond1", "0xcond2"]
    first = held[0]
    assert first.outcome == "Yes" and first.size == 1200.0
    assert first.avg_price == 0.42 and first.cur_price == 0.55
    assert first.current_value == 660.0 and first.cash_pnl == 156.0
    assert first.title.startswith("Will the incumbent")
    assert first.end_date is not None and first.end_date.year == 2026


def test_client_is_fail_soft_on_a_transient_transport_error() -> None:
    client = PolymarketPositionsClient(_RaisingTransport(TransientTransportError("503")))
    assert client.current_positions("0xwhale") == []  # data-api down → no positions, never a crash


def test_client_propagates_a_geo_block() -> None:
    client = PolymarketPositionsClient(_RaisingTransport(LiveVenueAccessError("geo")))
    with pytest.raises(LiveVenueAccessError):  # §8.6 must surface to the operator
        client.current_positions("0xwhale")


def test_client_drops_a_malformed_row_without_crashing() -> None:
    class _Partial:
        def fetch_positions(self, wallet: str) -> list[dict[str, Any]]:
            return [{"conditionId": "0xok", "outcome": "Yes"}]  # missing size/price fields

    held = PolymarketPositionsClient(_Partial()).current_positions("0xwhale")
    assert held == []  # the malformed row is dropped, not raised


def test_httpx_transport_queries_positions_by_user(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> _FakeResponse:
        calls.append({"url": url, "params": params})
        return _FakeResponse([{"conditionId": "0xc"}])

    monkeypatch.setattr(httpx, "get", fake_get)
    rows = HttpxPositionsTransport("https://data-api", use_live=True).fetch_positions("0xWALLET")

    assert rows == [{"conditionId": "0xc"}]
    assert calls[0]["url"] == "https://data-api/positions"
    assert calls[0]["params"] == {"user": "0xWALLET"}


def test_httpx_transport_refuses_a_live_call_when_use_live_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("live HTTP call made with USE_LIVE_VENUES off")

    monkeypatch.setattr(httpx, "get", _boom)
    with pytest.raises(LiveVenueAccessError):  # §8.6 defense-in-depth, before any network call
        HttpxPositionsTransport("https://data-api", use_live=False).fetch_positions("0xw")
