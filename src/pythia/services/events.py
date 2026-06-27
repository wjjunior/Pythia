"""Event read-model: assemble a classified EventGroup from the store, point-in-time (PYT-137)."""

from datetime import datetime

from sqlmodel import Session

from pythia.common import EventGroup, Market, MarketSnapshot
from pythia.db.repositories import MarketRepository, SnapshotRepository
from pythia.scoring.events import build_event_group


class EventService:
    """Build an EventGroup for an event_slug from its option-markets' latest snapshots <= asof."""

    def __init__(self, session: Session) -> None:
        self._markets = MarketRepository(session)
        self._snapshots = SnapshotRepository(session)

    def get_event_group(self, event_slug: str, asof: datetime) -> EventGroup | None:
        options: list[tuple[Market, MarketSnapshot]] = []
        for market in self._markets.list_by_event_slug(event_slug):
            snapshot = self._snapshots.latest_for_market_asof(market.id, asof=asof)
            if snapshot is not None:
                options.append((market, snapshot))
        if not options:
            return None
        return build_event_group(event_slug, options)
