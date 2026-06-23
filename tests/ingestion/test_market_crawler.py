"""Tests for the Polymarket market crawler: normalization, retries, VPN gating (offline)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pythia.common import Market, MarketDataPort, MarketSnapshot
from pythia.ingestion import (
    FixtureTransport,
    LiveVenueAccessError,
    MarketCrawler,
    MarketNotFoundError,
    TransientTransportError,
)
from pythia.ingestion.market_crawler import resolved_outcome, to_market

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "polymarket" / "gamma_markets.json"
TS = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
_RAW: list[dict[str, Any]] = json.loads(FIXTURE.read_text())


def _crawler() -> MarketCrawler:
    return MarketCrawler(FixtureTransport(FIXTURE), clock=lambda: TS, sleep=lambda _seconds: None)


class _FlakyTransport:
    def __init__(self, fail_times: int) -> None:
        self.calls = 0
        self._fail_times = fail_times

    def fetch_markets(
        self, *, closed: bool = False, end_date_max: str | None = None
    ) -> list[dict[str, Any]]:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise TransientTransportError("temporary")
        return _RAW


class _GeoBlockedTransport:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_markets(
        self, *, closed: bool = False, end_date_max: str | None = None
    ) -> list[dict[str, Any]]:
        self.calls += 1
        raise LiveVenueAccessError("enable the VPN")


class _StaticTransport:
    def __init__(self, markets: list[dict[str, Any]]) -> None:
        self._markets = markets

    def fetch_markets(
        self, *, closed: bool = False, end_date_max: str | None = None
    ) -> list[dict[str, Any]]:
        return self._markets


class _CapturingTransport:
    def __init__(self) -> None:
        self.end_date_max: str | None = "unset"
        self.closed: bool | None = None

    def fetch_markets(
        self, *, closed: bool = False, end_date_max: str | None = None
    ) -> list[dict[str, Any]]:
        self.end_date_max = end_date_max
        self.closed = closed
        return _RAW


def test_poll_requests_a_close_window_when_configured() -> None:
    transport = _CapturingTransport()
    crawler = MarketCrawler(transport, clock=lambda: TS, sleep=lambda _s: None, close_within_days=7)
    crawler.poll()
    assert transport.end_date_max == (TS + timedelta(days=7)).isoformat()


def test_poll_omits_the_close_window_by_default() -> None:
    transport = _CapturingTransport()
    MarketCrawler(transport, clock=lambda: TS, sleep=lambda _s: None).poll()
    assert transport.end_date_max is None


def test_poll_omits_the_close_window_when_days_is_non_positive() -> None:
    transport = _CapturingTransport()
    MarketCrawler(transport, clock=lambda: TS, sleep=lambda _s: None, close_within_days=0).poll()
    assert transport.end_date_max is None


def test_resolutions_ignore_the_close_window() -> None:
    transport = _CapturingTransport()
    crawler = MarketCrawler(transport, clock=lambda: TS, sleep=lambda _s: None, close_within_days=7)
    crawler.resolutions()
    assert transport.closed is True
    assert transport.end_date_max is None


def test_poll_drops_a_market_with_mismatched_outcomes_and_prices() -> None:
    malformed = {
        **_RAW[0],
        "outcomes": '["Maybe", "No", "Other"]',
        "outcomePrices": '["0.4", "0.6"]',
    }
    crawler = MarketCrawler(_StaticTransport([malformed]), clock=lambda: TS, sleep=lambda _s: None)
    assert crawler.poll() == []


def test_poll_drops_mismatched_lengths_even_when_yes_appears_first() -> None:
    # "yes" is first, so an early return would mask the length mismatch — the explicit
    # length check must still reject it (a strict-zip alone would not).
    malformed = {
        **_RAW[0],
        "outcomes": '["Yes", "No", "Other"]',
        "outcomePrices": '["0.6", "0.4"]',
    }
    crawler = MarketCrawler(_StaticTransport([malformed]), clock=lambda: TS, sleep=lambda _s: None)
    assert crawler.poll() == []


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


def test_to_market_reads_resolution_date_from_uma_end_date() -> None:
    # Trading closes at endDate but settlement is the UMA resolution date — they can be far apart.
    raw = {**_RAW[0], "umaEndDate": "2026-12-31T00:00:00Z"}
    market = to_market(raw, "00000000-0000-0000-0000-000000000001")
    assert market.resolves_at == datetime(2026, 12, 31, 0, 0, tzinfo=UTC)
    assert market.resolves_at != market.close_time


def test_to_market_leaves_resolution_date_empty_without_uma_end_date() -> None:
    market = to_market(_RAW[0], "00000000-0000-0000-0000-000000000001")
    assert market.resolves_at is None


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


def test_snapshot_market_id_matches_the_listed_market() -> None:
    crawler = _crawler()
    market = crawler.list_markets()[2]
    snapshot = crawler.snapshot(market.external_id)
    assert snapshot.market_id == market.id


def test_market_id_is_stable_across_polls() -> None:
    assert _crawler().poll()[0][0].id == _crawler().poll()[0][0].id


def test_snapshot_raises_clear_error_for_unknown_market() -> None:
    with pytest.raises(MarketNotFoundError):
        _crawler().snapshot("0xdoesnotexist")


def test_resolved_outcome_reads_a_clean_binary_winner() -> None:
    yes_won = {"outcomes": '["Yes", "No"]', "outcomePrices": '["1", "0"]'}
    no_won = {"outcomes": '["Yes", "No"]', "outcomePrices": '["0", "1"]'}
    assert resolved_outcome(yes_won) == 1
    assert resolved_outcome(no_won) == 0


def test_resolved_outcome_is_none_when_not_cleanly_resolved() -> None:
    tied = {"outcomes": '["Yes", "No"]', "outcomePrices": '["0.5", "0.5"]'}
    mismatched = {"outcomes": '["Yes", "No", "Maybe"]', "outcomePrices": '["1", "0"]'}
    assert resolved_outcome(tied) is None
    assert resolved_outcome(mismatched) is None


def test_resolutions_returns_outcomes_for_resolved_markets() -> None:
    resolved_raw = {
        **_RAW[0],
        "conditionId": "0xdone",
        "closed": True,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["1", "0"]',
        "endDate": "2026-06-18T19:00:00Z",
    }
    crawler = MarketCrawler(
        _StaticTransport([resolved_raw]), clock=lambda: TS, sleep=lambda _s: None
    )
    results = crawler.resolutions()
    assert len(results) == 1
    assert results[0].external_id == "0xdone"
    assert results[0].outcome == 1
    assert results[0].resolved_at == datetime(2026, 6, 18, 19, 0, tzinfo=UTC)
