"""APScheduler wiring for the market-crawl and venue-ingest heartbeats."""

from collections.abc import Callable
from contextlib import AbstractContextManager

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlmodel import Session

from pythia.common import Market, MarketSnapshot, Venue
from pythia.ingestion import MarketCrawler
from pythia.ingestion.venues import VenueClient
from pythia.worker.venue_ingest import ingest_tick

Sink = Callable[[list[tuple[Market, MarketSnapshot]]], None]
SessionFactory = Callable[[], AbstractContextManager[Session]]


def tick(crawler: MarketCrawler, sink: Sink) -> None:
    sink(crawler.poll())


def venue_tick(session_factory: SessionFactory, clients: list[tuple[Venue, VenueClient]]) -> None:
    with session_factory() as session:
        ingest_tick(session, clients)


def build_scheduler(
    crawler: MarketCrawler,
    sink: Sink,
    interval_seconds: int,
    *,
    session_factory: SessionFactory | None = None,
    venue_clients: list[tuple[Venue, VenueClient]] | None = None,
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
    return scheduler
