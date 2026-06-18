"""Tests for the Polymarket market crawler: normalization, retries, VPN gating (offline)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pythia.common import Market, MarketDataPort, MarketSnapshot
from pythia.ingestion import (
    FixtureTransport,
    LiveVenueAccessError,
    MarketCrawler,
    TransientTransportError,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "polymarket" / "gamma_markets.json"
TS = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
_RAW: list[dict[str, Any]] = json.loads(FIXTURE.read_text())


def _crawler() -> MarketCrawler:
    return MarketCrawler(FixtureTransport(FIXTURE), clock=lambda: TS, sleep=lambda _seconds: None)


class _FlakyTransport:
    def __init__(self, fail_times: int) -> None:
        self.calls = 0
        self._fail_times = fail_times

    def fetch_markets(self) -> list[dict[str, Any]]:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise TransientTransportError("temporary")
        return _RAW


class _GeoBlockedTransport:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_markets(self) -> list[dict[str, Any]]:
        self.calls += 1
        raise LiveVenueAccessError("enable the VPN")


def test_poll_normalises_markets_and_snapshots() -> None:
    pairs = _crawler().poll()
    assert len(pairs) == 5
    market, snapshot = pairs[0]
    assert isinstance(market, Market)
    assert isinstance(snapshot, MarketSnapshot)
    assert market.external_id.startswith("0x870bb248")
    assert market.question.startswith("Will Switzerland win on 2026-06-18")
    assert market.resolution_source is not None
    assert market.close_time == datetime(2026, 6, 18, 19, 0, tzinfo=UTC)
    assert market.resolved is False
    assert snapshot.market_id == market.id
    assert snapshot.ts == TS
    assert snapshot.yes_price == pytest.approx(0.9995)
    assert snapshot.spread == pytest.approx(0.001)
    assert snapshot.volume == pytest.approx(7119295.285122047)
    assert snapshot.liquidity == pytest.approx(2936866.42816)


def test_crawler_conforms_to_market_data_port() -> None:
    port: MarketDataPort = _crawler()
    markets = port.list_markets()
    assert len(markets) == 5
    assert all(isinstance(m, Market) for m in markets)


def test_snapshot_looked_up_by_external_id() -> None:
    crawler = _crawler()
    external_id = crawler.list_markets()[2].external_id
    snapshot = crawler.snapshot(external_id)
    assert snapshot.ts == TS


def test_repolling_yields_identical_snapshot_values() -> None:
    first = _crawler().poll()[0][1]
    second = _crawler().poll()[0][1]
    assert (first.yes_price, first.spread, first.volume, first.liquidity, first.ts) == (
        second.yes_price,
        second.spread,
        second.volume,
        second.liquidity,
        second.ts,
    )


def test_poll_retries_transient_errors() -> None:
    transport = _FlakyTransport(fail_times=2)
    crawler = MarketCrawler(transport, clock=lambda: TS, sleep=lambda _seconds: None, retries=3)
    pairs = crawler.poll()
    assert transport.calls == 3
    assert len(pairs) == 5


def test_poll_surfaces_live_venue_error_without_retry() -> None:
    transport = _GeoBlockedTransport()
    crawler = MarketCrawler(transport, clock=lambda: TS, sleep=lambda _seconds: None, retries=3)
    with pytest.raises(LiveVenueAccessError):
        crawler.poll()
    assert transport.calls == 1
