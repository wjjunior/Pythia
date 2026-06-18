"""Repositories mapping the §4.1 domain contracts to the §4.7 SQLModel persistence.

markets upsert idempotently by external_id (DO UPDATE on metadata); market_snapshots are
append-only (no update path); sources upsert by name. Contract ids are used as primary keys.
"""

import uuid
from collections.abc import Sequence

from sqlmodel import Session, col, select

from pythia.common import Estimate, Market, MarketSnapshot, Source
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


def _to_db_estimate(estimate: Estimate) -> models.Estimate:
    return models.Estimate(
        id=uuid.UUID(estimate.id),
        market_id=uuid.UUID(estimate.market_id),
        snapshot_id=uuid.UUID(estimate.snapshot_id),
        p_model=estimate.p_model,
        confidence=estimate.confidence,
        lower=estimate.lower,
        upper=estimate.upper,
        rationale=estimate.rationale,
        critic_notes=estimate.critic_notes,
        evidence_ids=[uuid.UUID(evidence_id) for evidence_id in estimate.evidence_ids],
        model_name=estimate.model_name,
    )


def _from_db_estimate(row: models.Estimate) -> Estimate:
    return Estimate(
        id=str(row.id),
        market_id=str(row.market_id),
        snapshot_id=str(row.snapshot_id),
        p_model=row.p_model,
        confidence=row.confidence,
        lower=row.lower,
        upper=row.upper,
        rationale=row.rationale,
        evidence_ids=[str(evidence_id) for evidence_id in row.evidence_ids],
        model_name=row.model_name,
        critic_notes=row.critic_notes,
    )


class EstimateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, estimate: Estimate) -> Estimate:
        row = _to_db_estimate(estimate)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _from_db_estimate(row)

    def get(self, estimate_id: str) -> Estimate | None:
        row = self._session.get(models.Estimate, uuid.UUID(estimate_id))
        return _from_db_estimate(row) if row is not None else None


def persist_crawl(session: Session, pairs: Sequence[tuple[Market, MarketSnapshot]]) -> None:
    markets = MarketRepository(session)
    snapshots = SnapshotRepository(session)
    for market, snapshot in pairs:
        markets.upsert(market)
        snapshots.append(snapshot)
