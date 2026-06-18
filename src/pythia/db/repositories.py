"""Repositories mapping the §4.1 domain contracts to the §4.7 SQLModel persistence.

markets upsert idempotently by external_id (DO UPDATE on metadata); market_snapshots are
append-only (no update path); sources upsert by name. Contract ids are used as primary keys.
"""

import uuid
from collections.abc import Sequence

from sqlmodel import Session, col, select

from pythia.common import Market, MarketSnapshot, Source
from pythia.db import models


def _to_db_market(market: Market) -> models.Market:
    return models.Market(
        id=uuid.UUID(market.id),
        external_id=market.external_id,
        question=market.question,
        category=market.category,
        resolution_rule=market.resolution_rule,
        resolution_source=market.resolution_source,
        close_time=market.close_time,
        resolved=market.resolved,
    )


def _from_db_market(row: models.Market) -> Market:
    return Market(
        id=str(row.id),
        external_id=row.external_id,
        question=row.question,
        category=row.category,
        resolution_rule=row.resolution_rule,
        resolution_source=row.resolution_source,
        close_time=row.close_time,
        resolved=row.resolved,
    )


def _to_db_snapshot(snapshot: MarketSnapshot) -> models.MarketSnapshot:
    return models.MarketSnapshot(
        id=uuid.UUID(snapshot.id),
        market_id=uuid.UUID(snapshot.market_id),
        ts=snapshot.ts,
        yes_price=snapshot.yes_price,
        spread=snapshot.spread,
        volume=snapshot.volume,
        liquidity=snapshot.liquidity,
    )


def _from_db_snapshot(row: models.MarketSnapshot) -> MarketSnapshot:
    return MarketSnapshot(
        id=str(row.id),
        market_id=str(row.market_id),
        ts=row.ts,
        yes_price=row.yes_price,
        spread=row.spread,
        volume=row.volume,
        liquidity=row.liquidity,
    )


def _to_db_source(source: Source) -> models.Source:
    return models.Source(
        id=uuid.UUID(source.id),
        name=source.name,
        kind=source.kind,
        reliability=source.reliability,
    )


def _from_db_source(row: models.Source) -> Source:
    return Source(id=str(row.id), name=row.name, kind=row.kind, reliability=row.reliability)


class MarketRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_external_id(self, external_id: str) -> Market | None:
        row = self._session.exec(
            select(models.Market).where(models.Market.external_id == external_id)
        ).one_or_none()
        return _from_db_market(row) if row is not None else None

    def upsert(self, market: Market) -> Market:
        row = self._session.exec(
            select(models.Market).where(models.Market.external_id == market.external_id)
        ).one_or_none()
        if row is None:
            row = _to_db_market(market)
            self._session.add(row)
        else:
            row.question = market.question
            row.category = market.category
            row.resolution_rule = market.resolution_rule
            row.resolution_source = market.resolution_source
            row.close_time = market.close_time
            row.resolved = market.resolved
        self._session.commit()
        self._session.refresh(row)
        return _from_db_market(row)


class SnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, snapshot: MarketSnapshot) -> MarketSnapshot:
        row = _to_db_snapshot(snapshot)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _from_db_snapshot(row)

    def list_for_market(self, market_id: str) -> list[MarketSnapshot]:
        rows = self._session.exec(
            select(models.MarketSnapshot)
            .where(models.MarketSnapshot.market_id == uuid.UUID(market_id))
            .order_by(col(models.MarketSnapshot.ts))
        ).all()
        return [_from_db_snapshot(row) for row in rows]


class SourceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_name(self, name: str) -> Source | None:
        row = self._session.exec(
            select(models.Source).where(models.Source.name == name)
        ).one_or_none()
        return _from_db_source(row) if row is not None else None

    def upsert(self, source: Source) -> Source:
        row = self._session.exec(
            select(models.Source).where(models.Source.name == source.name)
        ).one_or_none()
        if row is None:
            row = _to_db_source(source)
            self._session.add(row)
        else:
            row.kind = source.kind
            row.reliability = source.reliability
        self._session.commit()
        self._session.refresh(row)
        return _from_db_source(row)


def persist_crawl(session: Session, pairs: Sequence[tuple[Market, MarketSnapshot]]) -> None:
    markets = MarketRepository(session)
    snapshots = SnapshotRepository(session)
    for market, snapshot in pairs:
        markets.upsert(market)
        snapshots.append(snapshot)
