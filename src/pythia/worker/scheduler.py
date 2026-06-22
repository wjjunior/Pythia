"""APScheduler wiring for the crawl, venue-ingest, evidence-ingest and pipeline-run heartbeats."""

from collections.abc import Callable
from contextlib import AbstractContextManager

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlmodel import Session

from pythia.common import EmbeddingPort, Market, MarketSnapshot, Venue
from pythia.config import Settings
from pythia.ingestion import MarketCrawler
from pythia.ingestion.evidence import EvidenceTransport
from pythia.ingestion.venues import VenueClient
from pythia.orchestration import run_open_markets
from pythia.worker.evidence_ingest import ingest_tick as evidence_ingest_tick
from pythia.worker.venue_ingest import ingest_tick as venue_ingest_tick

Sink = Callable[[list[tuple[Market, MarketSnapshot]]], None]
SessionFactory = Callable[[], AbstractContextManager[Session]]


def tick(crawler: MarketCrawler, sink: Sink) -> None:
    sink(crawler.poll())


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
        run_open_markets(session, settings)


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
) -> BlockingScheduler:
    scheduler = BlockingScheduler()
    scheduler.add_job(
        lambda: tick(crawler, sink),
        "interval",
        seconds=interval_seconds,
        id="market-crawl",
    )
    if session_factory is not None and venue_clients:
        scheduler.add_job(
            lambda: venue_tick(session_factory, venue_clients),
            "interval",
            seconds=interval_seconds,
            id="venue-ingest",
        )
    if session_factory is not None and embedder is not None and evidence_transports:
        scheduler.add_job(
            lambda: evidence_tick(session_factory, embedder, evidence_transports),
            "interval",
            seconds=interval_seconds,
            id="evidence-ingest",
        )
    if session_factory is not None and settings is not None:
        scheduler.add_job(
            lambda: pipeline_tick(session_factory, settings),
            "interval",
            seconds=interval_seconds,
            id="pipeline-run",
        )
    return scheduler
