"""APScheduler wiring for the crawl, venue-ingest, evidence-ingest and pipeline-run heartbeats."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlmodel import Session

from pythia.common import EmbeddingPort, Market, MarketSnapshot, PositionsPort, Venue
from pythia.common.logging import get_logger
from pythia.config import Settings
from pythia.db.repositories import persist_crawl
from pythia.ingestion import MarketCrawler
from pythia.ingestion.evidence import EvidenceTransport
from pythia.ingestion.falcon import FalconPort
from pythia.ingestion.venues import VenueClient
from pythia.orchestration import make_funnel_sink, run_open_markets
from pythia.services.calibration import RecalibrationService
from pythia.worker.evidence_ingest import ingest_tick as evidence_ingest_tick
from pythia.worker.resolution_ingest import ingest_tick as resolution_ingest_tick
from pythia.worker.smartmoney_ingest import run_detection, run_positions_sweep, run_roster_review
from pythia.worker.venue_ingest import ingest_tick as venue_ingest_tick

_log = get_logger(__name__)

Sink = Callable[[list[tuple[Market, MarketSnapshot]]], None]
SessionFactory = Callable[[], AbstractContextManager[Session]]


def _now() -> datetime:
    return datetime.now(UTC)


def tick(crawler: MarketCrawler, sink: Sink) -> None:
    sink(crawler.poll())


def make_persist_sink(session_factory: SessionFactory) -> Sink:
    def sink(pairs: list[tuple[Market, MarketSnapshot]]) -> None:
        with session_factory() as session:
            persist_crawl(session, pairs)
        _log.info("market_crawl_tick", snapshots=len(pairs))

    return sink


def venue_tick(session_factory: SessionFactory, clients: list[tuple[Venue, VenueClient]]) -> None:
    with session_factory() as session:
        venue_ingest_tick(session, clients)


def evidence_tick(
    session_factory: SessionFactory,
    embedder: EmbeddingPort,
    transports: list[EvidenceTransport],
) -> None:
    with session_factory() as session:
        evidence_ingest_tick(session, embedder, transports)


def pipeline_tick(session_factory: SessionFactory, settings: Settings) -> None:
    # The SSE broadcaster is in-process (API process); a worker run writes scores but cannot
    # publish radar_changed — POST /api/run remains the on-demand + SSE path (§A.2, PYT-47).
    with session_factory() as session:
        run_open_markets(session, settings, funnel_sink=make_funnel_sink(session))


def resolution_tick(
    session_factory: SessionFactory, settings: Settings, crawler: MarketCrawler
) -> None:
    with session_factory() as session:
        resolution_ingest_tick(session, settings, crawler)


def recalibration_tick(
    session_factory: SessionFactory, settings: Settings, clock: Callable[[], datetime]
) -> None:
    # Refit per-category p_model→p_calibrated maps as of now (resolutions strictly before it). The
    # map's fit_asof = now; the pipeline applies it only to snapshots with ts >= now (PYT-111).
    with session_factory() as session:
        stored = RecalibrationService(session).fit_and_store(
            clock(),
            method=settings.recalibration_method,
            min_n=settings.recalibration_min_n,
        )
    _log.info("recalibration_fit_tick", categories=len(stored))


def smartmoney_detection_tick(
    session_factory: SessionFactory,
    settings: Settings,
    falcon: FalconPort,
    clock: Callable[[], datetime],
) -> None:
    with session_factory() as session:
        run_detection(session, settings, falcon, now=clock())


def smartmoney_roster_tick(
    session_factory: SessionFactory,
    settings: Settings,
    falcon: FalconPort,
    clock: Callable[[], datetime],
) -> None:
    with session_factory() as session:
        run_roster_review(session, settings, falcon, now=clock())


def smartmoney_positions_tick(
    session_factory: SessionFactory,
    positions: PositionsPort,
    clock: Callable[[], datetime],
) -> None:
    with session_factory() as session:
        run_positions_sweep(session, positions, now=clock())


def build_scheduler(
    crawler: MarketCrawler,
    sink: Sink,
    interval_seconds: int,
    *,
    session_factory: SessionFactory | None = None,
    venue_clients: list[tuple[Venue, VenueClient]] | None = None,
    embedder: EmbeddingPort | None = None,
    evidence_transports: list[EvidenceTransport] | None = None,
    settings: Settings | None = None,
    falcon: FalconPort | None = None,
    positions: PositionsPort | None = None,
    clock: Callable[[], datetime] = _now,
) -> BlockingScheduler:
    # next_run_time = now makes each job fire once immediately at startup, then on the interval.
    now = clock()
    scheduler = BlockingScheduler()
    scheduler.add_job(
        lambda: tick(crawler, sink),
        "interval",
        seconds=interval_seconds,
        id="market-crawl",
        next_run_time=now,
    )
    if session_factory is not None and venue_clients:
        scheduler.add_job(
            lambda: venue_tick(session_factory, venue_clients),
            "interval",
            seconds=interval_seconds,
            id="venue-ingest",
            next_run_time=now,
        )
    if session_factory is not None and embedder is not None and evidence_transports:
        scheduler.add_job(
            lambda: evidence_tick(session_factory, embedder, evidence_transports),
            "interval",
            seconds=interval_seconds,
            id="evidence-ingest",
            next_run_time=now,
        )
    if session_factory is not None and settings is not None:
        scheduler.add_job(
            lambda: pipeline_tick(session_factory, settings),
            "interval",
            seconds=settings.pipeline_interval_seconds,  # estimation cadence, budget-bound (168)
            id="pipeline-run",
            next_run_time=now,
        )
        scheduler.add_job(
            lambda: resolution_tick(session_factory, settings, crawler),
            "interval",
            seconds=interval_seconds,
            id="resolution-ingest",
            next_run_time=now,
        )
        scheduler.add_job(
            lambda: recalibration_tick(session_factory, settings, clock),
            "interval",
            seconds=settings.recalibration_interval_seconds,
            id="recalibration-fit",
            next_run_time=now,
        )
    if session_factory is not None and settings is not None and falcon is not None:
        scheduler.add_job(
            lambda: smartmoney_detection_tick(session_factory, settings, falcon, clock),
            "interval",
            seconds=interval_seconds,
            id="smartmoney-detection",
            next_run_time=now,
        )
        scheduler.add_job(
            lambda: smartmoney_roster_tick(session_factory, settings, falcon, clock),
            "interval",
            seconds=settings.roster_review_interval_seconds,
            id="smartmoney-roster-review",
            next_run_time=now,
        )
    if session_factory is not None and positions is not None:
        scheduler.add_job(
            lambda: smartmoney_positions_tick(session_factory, positions, clock),
            "interval",
            seconds=interval_seconds,
            id="smartmoney-positions",
            next_run_time=now,
        )
    return scheduler
