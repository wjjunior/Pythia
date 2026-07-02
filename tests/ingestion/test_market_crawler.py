"""Tests for the Polymarket market crawler: normalization, retries, VPN gating (offline)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from pythia.common import Market, MarketDataPort, MarketSnapshot, ResolutionSourceType
from pythia.ingestion import (
    FixtureTransport,
    HttpxTransport,
    LiveVenueAccessError,
    MarketCrawler,
    MarketNotFoundError,
    TransientTransportError,
)
from pythia.ingestion.market_crawler import _event_slug, resolved_outcome, to_market

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
        self,
        *,
        closed: bool = False,
        end_date_max: str | None = None,
        end_date_min: str | None = None,
    ) -> list[dict[str, Any]]:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise TransientTransportError("temporary")
        return _RAW

    def fetch_event_markets(self, slug: str) -> list[dict[str, Any]]:
        return []


class _GeoBlockedTransport:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_markets(
        self,
        *,
        closed: bool = False,
        end_date_max: str | None = None,
        end_date_min: str | None = None,
    ) -> list[dict[str, Any]]:
        self.calls += 1
        raise LiveVenueAccessError("enable the VPN")

    def fetch_event_markets(self, slug: str) -> list[dict[str, Any]]:
        return []


class _StaticTransport:
    def __init__(self, markets: list[dict[str, Any]]) -> None:
        self._markets = markets

    def fetch_markets(
        self,
        *,
        closed: bool = False,
        end_date_max: str | None = None,
        end_date_min: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._markets

    def fetch_event_markets(self, slug: str) -> list[dict[str, Any]]:
        return []


class _CapturingTransport:
    def __init__(self) -> None:
        self.end_date_max: str | None = "unset"
        self.end_date_min: str | None = "unset"
        self.closed: bool | None = None

    def fetch_markets(
        self,
        *,
        closed: bool = False,
        end_date_max: str | None = None,
        end_date_min: str | None = None,
    ) -> list[dict[str, Any]]:
        self.end_date_max = end_date_max
        self.end_date_min = end_date_min
        self.closed = closed
        return _RAW

    def fetch_event_markets(self, slug: str) -> list[dict[str, Any]]:
        return []


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
    assert transport.end_date_min is None  # resolved markets ended in the past — never floor them


def test_poll_floors_the_open_crawl_at_now_to_skip_already_ended_markets() -> None:
    transport = _CapturingTransport()
    MarketCrawler(transport, clock=lambda: TS, sleep=lambda _s: None).poll()
    assert transport.end_date_min == TS.isoformat()


class _FakeResponse:
    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict[str, Any]]:
        return self._payload


def test_httpx_open_crawl_requests_soonest_resolving_liquid_markets_paginated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [[{"i": n} for n in range(100)], [{"i": n} for n in range(100, 150)]]  # full + short
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> _FakeResponse:
        calls.append(params)
        idx = int(params["offset"]) // 100
        return _FakeResponse(pages[idx] if idx < len(pages) else [])

    monkeypatch.setattr(httpx, "get", fake_get)
    transport = HttpxTransport(
        "https://gamma", use_live=True, limit=100, volume_min=1000.0, max_markets=500
    )
    out = transport.fetch_markets(
        closed=False, end_date_max="2026-07-01T00:00:00", end_date_min="2026-06-24T00:00:00"
    )

    assert [c["offset"] for c in calls] == [0, 100]  # paginated until a short page
    assert len(out) == 150
    first = calls[0]
    assert first["closed"] == "false"
    assert first["order"] == "endDate" and first["ascending"] == "true"
    assert first["end_date_min"] == "2026-06-24T00:00:00"
    assert first["end_date_max"] == "2026-07-01T00:00:00"
    assert first["volume_num_min"] == 1000.0


def test_httpx_resolution_crawl_is_a_single_unordered_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> _FakeResponse:
        calls.append(params)
        return _FakeResponse([{"x": 1}])

    monkeypatch.setattr(httpx, "get", fake_get)
    HttpxTransport("https://gamma", use_live=True).fetch_markets(closed=True)

    assert len(calls) == 1
    assert calls[0]["closed"] == "true"
    assert "order" not in calls[0] and "end_date_min" not in calls[0] and "offset" not in calls[0]


def test_httpx_fetch_event_markets_restores_the_event_back_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Gamma's /events nests markets WITHOUT the `events` back-reference; the transport restores it
    # so a backfilled leg derives the event slug, not its own market slug (PYT-148).
    event = {"slug": "evt", "markets": [{"conditionId": "0xC", "slug": "evt-bucket"}]}

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> _FakeResponse:
        return _FakeResponse([event])

    monkeypatch.setattr(httpx, "get", fake_get)
    markets = HttpxTransport("https://gamma", use_live=True).fetch_event_markets("evt")

    assert _event_slug(markets[0]) == "evt"  # was falling back to the market's own slug


def test_httpx_fetch_market_by_condition_queries_markets_and_filters_by_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PYT-156: a single open market by condition_id, to ingest a smart-money trigger off-slate.
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> _FakeResponse:
        calls.append(params)
        return _FakeResponse([{"conditionId": "0xother"}, {"conditionId": "0xC"}])

    monkeypatch.setattr(httpx, "get", fake_get)
    market = HttpxTransport("https://gamma", use_live=True).fetch_market_by_condition("0xC")

    assert market == {"conditionId": "0xC"}  # the matching row, not data[0]
    assert calls[0]["condition_ids"] == "0xC" and calls[0]["closed"] == "false"
    assert calls[0]["include_tag"] == "true"  # mirrors the open crawl's tag fallback


def test_httpx_fetch_market_by_condition_include_closed_drops_the_open_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PYT-181: stale settlement re-checks already-closed markets; closed=false would hide them.
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> _FakeResponse:
        calls.append(params)
        return _FakeResponse([{"conditionId": "0xC", "closed": True}])

    monkeypatch.setattr(httpx, "get", fake_get)
    market = HttpxTransport("https://gamma", use_live=True).fetch_market_by_condition(
        "0xC", include_closed=True
    )

    assert market == {"conditionId": "0xC", "closed": True}
    assert "closed" not in calls[0]  # no open-only filter, so a closed market is returned


def test_httpx_fetch_market_by_condition_returns_none_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "get", lambda url, params, timeout: _FakeResponse([]))
    assert HttpxTransport("https://gamma", use_live=True).fetch_market_by_condition("0xX") is None


def test_httpx_transport_refuses_a_live_call_when_use_live_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # §8.6 defense-in-depth (PYT-115): the guard refuses BEFORE any network call, independent of the
    # worker entrypoint gate — httpx.get must never run with the flag off.
    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("live HTTP call made with USE_LIVE_VENUES off")

    monkeypatch.setattr(httpx, "get", _boom)
    with pytest.raises(LiveVenueAccessError):
        HttpxTransport("https://gamma", use_live=False).fetch_markets()


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


def test_poll_dedupes_a_market_gamma_returns_more_than_once() -> None:
    # Gamma lists a market under multiple events; a duplicate conditionId in one batch must yield a
    # single snapshot, not two identical deterministic ids that abort the crawl insert (PYT-134).
    transport = _StaticTransport([_RAW[0], _RAW[0]])
    pairs = MarketCrawler(transport, clock=lambda: TS, sleep=lambda _s: None).poll()
    assert len(pairs) == 1
    assert pairs[0][0].external_id == _RAW[0]["conditionId"]


def test_poll_prices_a_binary_market_without_a_yes_outcome_from_outcome_prices() -> None:
    # Bitcoin Up/Down style: no literal "Yes" outcome and no lastTradePrice, but priceable from
    # outcomePrices — the affirmative side is the first outcome (PYT-135).
    up_down = {k: v for k, v in _RAW[0].items() if k != "lastTradePrice"}
    up_down |= {"outcomes": '["Up", "Down"]', "outcomePrices": '["0.62", "0.38"]'}
    pairs = MarketCrawler(
        _StaticTransport([up_down]), clock=lambda: TS, sleep=lambda _s: None
    ).poll()
    assert len(pairs) == 1  # priced, not dropped
    assert pairs[0][1].yes_price == pytest.approx(0.62)  # the affirmative ("Up") price


def test_to_market_captures_neg_risk() -> None:
    # PYT-137: the fixture is a negRisk (mutually-exclusive) market; the flag is stored at ingest.
    assert to_market(_RAW[0], "m").neg_risk is True
    assert to_market({**_RAW[0], "negRisk": False}, "m").neg_risk is False


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


def test_to_market_normalises_a_date_only_resolution_to_utc() -> None:
    # umaEndDateIso can be a date-only string; it must still yield a tz-aware datetime, or radar
    # day-deltas and SQL comparisons against a UTC asof raise on naive/aware mismatch.
    raw = {**_RAW[0], "umaEndDateIso": "2026-12-31"}
    market = to_market(raw, "00000000-0000-0000-0000-000000000001")
    assert market.resolves_at == datetime(2026, 12, 31, 0, 0, tzinfo=UTC)
    assert market.resolves_at is not None and market.resolves_at.tzinfo is not None


def test_to_market_classifies_and_stores_the_resolution_source_type() -> None:
    # PYT-122: the resolution-source type is classified at ingest and stored, not re-derived at
    # score time, so P_resol grading reads a queryable value that never drifts under the classifier.
    mid = "00000000-0000-0000-0000-000000000001"
    chainlink = to_market({**_RAW[0], "resolutionSource": "Chainlink price feed"}, mid)
    assert chainlink.resolution_source_type is ResolutionSourceType.CHAINLINK
    no_source = to_market({**_RAW[0], "resolutionSource": None}, mid)
    assert no_source.resolution_source_type is ResolutionSourceType.SUBJECTIVE_UMA


def test_to_market_reads_event_slug_from_the_event() -> None:
    # The event slug (not the market slug) is what /event/<slug> resolves to on Polymarket.
    market = to_market(_RAW[0], "00000000-0000-0000-0000-000000000001")
    assert market.event_slug == "fifwc-che-bih-2026-06-18"


def test_to_market_falls_back_to_market_slug_without_an_event() -> None:
    raw = {**_RAW[0], "events": []}
    market = to_market(raw, "00000000-0000-0000-0000-000000000001")
    assert market.event_slug == "fifwc-che-bih-2026-06-18-che"


def test_to_market_leaves_event_slug_empty_without_any_slug() -> None:
    raw = {**_RAW[0], "events": [], "slug": ""}
    market = to_market(raw, "00000000-0000-0000-0000-000000000001")
    assert market.event_slug is None


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


def _raw_market(
    condition_id: str, slug: str, *, neg_risk: bool = True, yes: float = 0.3, closed: bool = False
) -> dict[str, Any]:
    return {
        "conditionId": condition_id,
        "question": f"{condition_id}?",
        "category": "sports",
        "description": "rule",
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps([str(yes), str(round(1.0 - yes, 4))]),
        "lastTradePrice": str(yes),
        "spread": 0.0,
        "volumeNum": 1000.0,
        "liquidityNum": 1000.0,
        "events": [{"slug": slug}],
        "negRisk": neg_risk,
        "closed": closed,
    }


class _EventTransport:
    """Serves a volume-filtered page plus a per-event full leg set (PYT-147 backfill)."""

    def __init__(self, page: list[dict[str, Any]], events: dict[str, list[dict[str, Any]]]) -> None:
        self._page = page
        self._events = events
        self.event_slugs: list[str] = []

    def fetch_markets(
        self,
        *,
        closed: bool = False,
        end_date_max: str | None = None,
        end_date_min: str | None = None,
    ) -> list[dict[str, Any]]:
        return [] if closed else self._page

    def fetch_event_markets(self, slug: str) -> list[dict[str, Any]]:
        self.event_slugs.append(slug)
        return self._events.get(slug, [])


def _poll(transport: _EventTransport) -> list[tuple[Market, MarketSnapshot]]:
    return MarketCrawler(transport, clock=lambda: TS, sleep=lambda _seconds: None).poll()


def test_poll_backfills_low_volume_legs_of_a_negrisk_event() -> None:
    page = [_raw_market("0xA", "evt"), _raw_market("0xB", "evt")]  # liquid legs survive the filter
    full = [*page, _raw_market("0xC", "evt")]  # the dropped low-volume leg
    transport = _EventTransport(page=page, events={"evt": full})

    pairs = _poll(transport)

    assert sorted(m.external_id for m, _ in pairs) == ["0xA", "0xB", "0xC"]  # event completed
    assert {m.event_slug for m, _ in pairs} == {"evt"}  # backfilled legs join the event group
    assert transport.event_slugs == ["evt"]  # one /events call, deduped by slug
    assert len({snapshot.id for _, snapshot in pairs}) == 3  # no duplicate snapshots


def test_poll_does_not_backfill_non_negrisk_events() -> None:
    page = [_raw_market("0xA", "evt", neg_risk=False)]
    full = [*page, _raw_market("0xB", "evt", neg_risk=False)]
    transport = _EventTransport(page=page, events={"evt": full})

    pairs = _poll(transport)

    assert [m.external_id for m, _ in pairs] == ["0xA"]
    assert transport.event_slugs == []  # ladders / non-negRisk events are out of scope


def test_poll_skips_closed_legs_when_backfilling() -> None:
    page = [_raw_market("0xA", "evt")]
    full = [*page, _raw_market("0xC", "evt", closed=True)]
    transport = _EventTransport(page=page, events={"evt": full})

    pairs = _poll(transport)

    assert [m.external_id for m, _ in pairs] == ["0xA"]  # a resolved leg is not ingested


def test_poll_degrades_to_the_page_when_backfill_fails() -> None:
    page = [_raw_market("0xA", "evt")]

    class _FailingEvents(_EventTransport):
        def fetch_event_markets(self, slug: str) -> list[dict[str, Any]]:
            raise TransientTransportError("boom")

    pairs = _poll(_FailingEvents(page=page, events={}))

    assert [m.external_id for m, _ in pairs] == ["0xA"]  # a backfill failure never crashes poll


def test_poll_degrades_when_event_backfill_returns_a_client_error() -> None:
    page = [_raw_market("0xA", "evt")]

    class _ClientErrorEvents(_EventTransport):
        def fetch_event_markets(self, slug: str) -> list[dict[str, Any]]:
            request = httpx.Request("GET", "https://gamma/events")
            raise httpx.HTTPStatusError(
                "404", request=request, response=httpx.Response(404, request=request)
            )

    pairs = _poll(_ClientErrorEvents(page=page, events={}))

    assert [m.external_id for m, _ in pairs] == ["0xA"]  # a 4xx on /events must not crash the crawl


def test_poll_skips_a_priceless_backfilled_leg_without_warning() -> None:
    # PYT-150: an untraded /events leg has no outcomePrices; drop it quietly, not as "malformed".
    page = [_raw_market("0xA", "evt")]
    valid = _raw_market("0xB", "evt")
    untraded = {**_raw_market("0xC", "evt"), "outcomePrices": None}
    transport = _EventTransport(page=page, events={"evt": [*page, valid, untraded]})

    with capture_logs() as logs:
        pairs = _poll(transport)

    assert sorted(m.external_id for m, _ in pairs) == ["0xA", "0xB"]  # 0xB backfilled, 0xC dropped
    assert not any(
        entry["event"] == "malformed_market_dropped" for entry in logs
    )  # dropped quietly
