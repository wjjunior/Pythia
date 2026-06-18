"""Tests for the crawl scheduler wiring (offline)."""

from datetime import UTC, datetime
from pathlib import Path

from pythia.common import Market, MarketSnapshot
from pythia.ingestion import FixtureTransport, MarketCrawler
from pythia.worker.scheduler import build_scheduler, tick

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "polymarket" / "gamma_markets.json"
TS = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


def _crawler() -> MarketCrawler:
    return MarketCrawler(FixtureTransport(FIXTURE), clock=lambda: TS, sleep=lambda _seconds: None)


def test_tick_passes_poll_results_to_the_sink() -> None:
    captured: list[tuple[Market, MarketSnapshot]] = []
    tick(_crawler(), captured.extend)
    assert len(captured) == 5


def test_build_scheduler_registers_the_crawl_job() -> None:
    scheduler = build_scheduler(_crawler(), lambda _pairs: None, interval_seconds=300)
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == "market-crawl"
