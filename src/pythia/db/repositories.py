"""Repositories mapping the §4.1 domain contracts to the §4.7 SQLModel persistence.

markets upsert idempotently by external_id (DO UPDATE on metadata); market_snapshots are
append-only (no update path); sources upsert by name. Contract ids are used as primary keys.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from sqlalchemy import func
from sqlmodel import Session, col, select

from pythia.common import (
    AuditEvent,
    CostEvent,
    Estimate,
    Evidence,
    Market,
    MarketSnapshot,
    MemberForecast,
    PaperTrade,
    Score,
    Side,
    Source,
    TradeStatus,
)
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

    def list(self, *, category: str | None = None, status: str | None = None) -> list[Market]:
        statement = select(models.Market)
        if category is not None:
            statement = statement.where(models.Market.category == category)
        if status == "resolved":
            statement = statement.where(col(models.Market.resolved).is_(True))
        elif status == "open":
            statement = statement.where(col(models.Market.resolved).is_(False))
        rows = self._session.exec(statement.order_by(col(models.Market.created_at))).all()
        return [_from_db_market(row) for row in rows]

    def get(self, market_id: str) -> Market | None:
        row = self._session.get(models.Market, uuid.UUID(market_id))
        return _from_db_market(row) if row is not None else None


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

    def get(self, source_id: str) -> Source | None:
        row = self._session.get(models.Source, uuid.UUID(source_id))
        return _from_db_source(row) if row is not None else None

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

    def latest_for_market(self, market_id: str) -> Estimate | None:
        row = self._session.exec(
            select(models.Estimate)
            .where(models.Estimate.market_id == uuid.UUID(market_id))
            .order_by(col(models.Estimate.created_at).desc(), col(models.Estimate.id).desc())
        ).first()
        return _from_db_estimate(row) if row is not None else None


class EstimateMemberRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(self, estimate_id: str, members: list[MemberForecast]) -> None:
        if not members:
            return
        for member in members:
            self._session.add(
                models.EstimateMember(
                    estimate_id=uuid.UUID(estimate_id),
                    model=member.model,
                    p_member=member.p_member,
                    weight=member.weight,
                )
            )
        self._session.commit()

    def list_for_estimate(self, estimate_id: str) -> list[MemberForecast]:
        rows = self._session.exec(
            select(models.EstimateMember)
            .where(models.EstimateMember.estimate_id == uuid.UUID(estimate_id))
            .order_by(col(models.EstimateMember.model))
        ).all()
        return [
            MemberForecast(model=row.model, p_member=row.p_member, weight=row.weight)
            for row in rows
        ]


def _to_db_score(score: Score) -> models.Score:
    return models.Score(
        estimate_id=uuid.UUID(score.estimate_id),
        edge=score.edge,
        edge_net=score.edge_net,
        ev_unit=score.ev_unit,
        p_resol=score.p_resol,
        p_liq=score.p_liq,
        p_time=score.p_time,
        p_corr=score.p_corr,
        opportunity_score=score.opportunity_score,
        kelly_fraction=score.kelly_fraction,
    )


def _from_db_score(row: models.Score) -> Score:
    return Score(
        estimate_id=str(row.estimate_id),
        edge=row.edge,
        edge_net=row.edge_net,
        ev_unit=row.ev_unit,
        p_resol=row.p_resol,
        p_liq=row.p_liq,
        p_time=row.p_time,
        p_corr=row.p_corr,
        opportunity_score=row.opportunity_score,
        kelly_fraction=row.kelly_fraction,
    )


class ScoreRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, score: Score) -> Score:
        row = _to_db_score(score)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _from_db_score(row)

    def get_by_estimate(self, estimate_id: str) -> Score | None:
        row = self._session.exec(
            select(models.Score).where(models.Score.estimate_id == uuid.UUID(estimate_id))
        ).one_or_none()
        return _from_db_score(row) if row is not None else None

    def radar(self, limit: int) -> list[tuple[Score, Estimate, Market]]:
        rows = self._session.exec(
            select(models.Score, models.Estimate, models.Market)
            .join(models.Estimate, col(models.Score.estimate_id) == col(models.Estimate.id))
            .join(models.Market, col(models.Estimate.market_id) == col(models.Market.id))
            .order_by(col(models.Score.opportunity_score).desc())
            .limit(limit)
        ).all()
        return [
            (_from_db_score(score), _from_db_estimate(estimate), _from_db_market(market))
            for score, estimate, market in rows
        ]


class PipelineRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def open(self, market_id: str | None, routing: Mapping[str, str]) -> str:
        row = models.PipelineRun(
            market_id=uuid.UUID(market_id) if market_id is not None else None,
            routing=dict(routing),
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return str(row.id)

    def finish(self, run_id: str, *, status: str) -> None:
        row = self._session.get(models.PipelineRun, uuid.UUID(run_id))
        if row is None:
            return
        row.status = status
        row.finished_at = datetime.now(UTC)
        self._session.add(row)
        self._session.commit()


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, event: AuditEvent) -> None:
        row = models.AuditEvent(
            kind=event.kind,
            market_id=uuid.UUID(event.market_id) if event.market_id is not None else None,
            payload=event.payload,
        )
        if event.ts is not None:
            row.ts = event.ts
        self._session.add(row)
        self._session.commit()

    def list_for_market(self, market_id: str) -> list[AuditEvent]:
        rows = self._session.exec(
            select(models.AuditEvent)
            .where(models.AuditEvent.market_id == uuid.UUID(market_id))
            .order_by(col(models.AuditEvent.ts), col(models.AuditEvent.id))
        ).all()
        return [
            AuditEvent(
                kind=row.kind,
                payload=row.payload,
                market_id=str(row.market_id) if row.market_id is not None else None,
                ts=row.ts,
            )
            for row in rows
        ]


def _to_db_cost(
    event: CostEvent,
    *,
    pipeline_run_id: str | None,
    market_id: str | None,
    estimate_id: str | None,
) -> models.CostEvent:
    return models.CostEvent(
        pipeline_run_id=uuid.UUID(pipeline_run_id) if pipeline_run_id is not None else None,
        market_id=uuid.UUID(market_id) if market_id is not None else None,
        estimate_id=uuid.UUID(estimate_id) if estimate_id is not None else None,
        agent=event.agent,
        task=event.task,
        provider=event.provider,
        model=event.model,
        input_tokens=event.input_tokens,
        output_tokens=event.output_tokens,
        cached_tokens=event.cached_tokens,
        cost_usd=event.cost_usd,
        latency_ms=event.latency_ms,
        correlation_id=event.correlation_id,
    )


class CostRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        event: CostEvent,
        *,
        pipeline_run_id: str | None = None,
        market_id: str | None = None,
        estimate_id: str | None = None,
    ) -> None:
        self._session.add(
            _to_db_cost(
                event,
                pipeline_run_id=pipeline_run_id,
                market_id=market_id,
                estimate_id=estimate_id,
            )
        )
        self._session.commit()

    def total(self) -> float:
        return float(
            self._session.exec(
                select(func.coalesce(func.sum(models.CostEvent.cost_usd), 0.0))
            ).one()
        )

    def daily_total(self, since: datetime) -> float:
        statement = select(func.coalesce(func.sum(models.CostEvent.cost_usd), 0.0)).where(
            col(models.CostEvent.ts) >= since
        )
        return float(self._session.exec(statement).one())

    def run_total(self, pipeline_run_id: str) -> float:
        statement = select(func.coalesce(func.sum(models.CostEvent.cost_usd), 0.0)).where(
            col(models.CostEvent.pipeline_run_id) == uuid.UUID(pipeline_run_id)
        )
        return float(self._session.exec(statement).one())

    def by_agent(self) -> dict[str, float]:
        statement = select(models.CostEvent.agent, func.sum(models.CostEvent.cost_usd)).group_by(
            col(models.CostEvent.agent)
        )
        return {agent: float(total) for agent, total in self._session.exec(statement).all()}

    def by_model(self) -> dict[str, float]:
        statement = select(models.CostEvent.model, func.sum(models.CostEvent.cost_usd)).group_by(
            col(models.CostEvent.model)
        )
        return {model: float(total) for model, total in self._session.exec(statement).all()}

    def cost_per_scored_opportunity(self) -> float:
        scored = self._session.exec(select(func.count()).select_from(models.Score)).one()
        return self.total() / scored if scored else 0.0


def _from_db_trade(row: models.PaperTrade) -> PaperTrade:
    return PaperTrade(
        id=str(row.id),
        market_id=str(row.market_id),
        estimate_id=str(row.estimate_id),
        side=Side(row.side),
        entry_price=row.entry_price,
        size=row.size,
        status=TradeStatus(row.status),
        opened_at=row.opened_at,
        resolved_at=row.resolved_at,
        pnl=row.pnl,
    )


class PaperTradeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_all(self) -> list[PaperTrade]:
        rows = self._session.exec(
            select(models.PaperTrade).order_by(col(models.PaperTrade.opened_at))
        ).all()
        return [_from_db_trade(row) for row in rows]

    def list_open(self) -> list[PaperTrade]:
        rows = self._session.exec(
            select(models.PaperTrade).where(models.PaperTrade.status == TradeStatus.OPEN)
        ).all()
        return [_from_db_trade(row) for row in rows]

    def realized_pnl(self) -> float:
        statement = select(func.coalesce(func.sum(models.PaperTrade.pnl), 0.0)).where(
            models.PaperTrade.status == TradeStatus.RESOLVED
        )
        return float(self._session.exec(statement).one() or 0.0)

    def locked_in_open(self) -> float:
        statement = select(func.coalesce(func.sum(models.PaperTrade.size), 0.0)).where(
            models.PaperTrade.status == TradeStatus.OPEN
        )
        return float(self._session.exec(statement).one())

    def open_positions(self) -> list[tuple[float, str, str, datetime]]:
        rows = self._session.exec(
            select(
                models.PaperTrade.size,
                models.Market.category,
                models.PaperTrade.market_id,
                models.PaperTrade.opened_at,
            )
            .join(models.Market, col(models.PaperTrade.market_id) == col(models.Market.id))
            .where(models.PaperTrade.status == TradeStatus.OPEN)
        ).all()
        return [
            (size, category, str(market_id), opened_at)
            for size, category, market_id, opened_at in rows
            if opened_at is not None
        ]


def _from_db_evidence(row: models.Evidence) -> Evidence:
    return Evidence(
        id=str(row.id),
        market_id=str(row.market_id),
        source_id=str(row.source_id),
        observed_at=row.observed_at,
        url=row.url,
        content=row.content,
    )


class EvidenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_ids(self, evidence_ids: list[str]) -> list[Evidence]:
        if not evidence_ids:
            return []
        rows = self._session.exec(
            select(models.Evidence).where(
                col(models.Evidence.id).in_([uuid.UUID(eid) for eid in evidence_ids])
            )
        ).all()
        by_id = {str(row.id): _from_db_evidence(row) for row in rows}
        return [by_id[eid] for eid in evidence_ids if eid in by_id]


def persist_crawl(session: Session, pairs: Sequence[tuple[Market, MarketSnapshot]]) -> None:
    markets = MarketRepository(session)
    snapshots = SnapshotRepository(session)
    for market, snapshot in pairs:
        markets.upsert(market)
        snapshots.append(snapshot)
