"""Tests for the crawl scheduler wiring (offline)."""

from datetime import UTC, datetime
from pathlib import Path

from pythia.common import Market, MarketSnapshot
from pythia.config import Settings
from pythia.db.session import session_scope
from pythia.ingestion import FixtureTransport, MarketCrawler
from pythia.ingestion.falcon import FalconClient, FixtureFalconTransport
from pythia.worker.scheduler import build_scheduler, tick

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "polymarket" / "gamma_markets.json"
FALCON_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "falcon"
TS = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


def _crawler() -> MarketCrawler:
    return MarketCrawler(FixtureTransport(FIXTURE), clock=lambda: TS, sleep=lambda _seconds: None)


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def test_tick_passes_poll_results_to_the_sink() -> None:
    captured: list[tuple[Market, MarketSnapshot]] = []
    tick(_crawler(), captured.extend)
    assert len(captured) == 5


def test_build_scheduler_registers_the_crawl_job() -> None:
    scheduler = build_scheduler(_crawler(), lambda _pairs: None, interval_seconds=300)
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == "market-crawl"


def test_build_scheduler_registers_pipeline_and_resolution_jobs_with_settings() -> None:
    scheduler = build_scheduler(
        _crawler(),
        lambda _pairs: None,
        interval_seconds=300,
        session_factory=session_scope,
        settings=_settings(),
    )
    ids = {job.id for job in scheduler.get_jobs()}
    assert ids == {"market-crawl", "pipeline-run", "resolution-ingest", "recalibration-fit"}


def test_build_scheduler_registers_smartmoney_jobs_when_falcon_is_wired() -> None:
    scheduler = build_scheduler(
        _crawler(),
        lambda _pairs: None,
        interval_seconds=300,
        session_factory=session_scope,
        settings=_settings(),
        falcon=FalconClient(FixtureFalconTransport(FALCON_FIXTURES)),
    )
    ids = {job.id for job in scheduler.get_jobs()}
    assert {"smartmoney-detection", "smartmoney-roster-review"} <= ids


def test_build_scheduler_runs_each_job_immediately_at_startup() -> None:
    fixed = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)
    scheduler = build_scheduler(
        _crawler(),
        lambda _pairs: None,
        interval_seconds=300,
        session_factory=session_scope,
        settings=_settings(),
        clock=lambda: fixed,
    )
    jobs = scheduler.get_jobs()
    assert jobs
    assert all(job.next_run_time == fixed for job in jobs)
