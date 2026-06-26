"""Repositories mapping the §4.1 domain contracts to the §4.7 SQLModel persistence.

markets upsert idempotently by external_id (DO UPDATE on metadata); market_snapshots are
append-only (no update path); sources upsert by name. Contract ids are used as primary keys.
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_
from sqlmodel import Session, col, select

from pythia.common import (
    AuditEvent,
    AuditKind,
    CandidateStatus,
    Cohort,
    CostEvent,
    Estimate,
    Evidence,
    FailureClass,
    ImprovementAction,
    ImprovementCandidate,
    Market,
    MarketSnapshot,
    MarketStatus,
    MemberForecast,
    PaperTrade,
    Postmortem,
    Score,
    Side,
    Source,
    TradeStatus,
    Venue,
    VenueQuote,
)
from pythia.common.ids import deterministic_id
from pythia.db import models
from pythia.eval.recalibration import CalibrationMap, as_method
from pythia.risk.models import RiskDecision, RiskReason


def _to_db_market(market: Market) -> models.Market:
    return models.Market(
        id=uuid.UUID(market.id),
        external_id=market.external_id,
        question=market.question,
        category=market.category,
        resolution_rule=market.resolution_rule,
        resolution_source=market.resolution_source,
        event_slug=market.event_slug,
        close_time=market.close_time,
        resolves_at=market.resolves_at,
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
        event_slug=row.event_slug,
        close_time=row.close_time,
        resolves_at=row.resolves_at,
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
            row.event_slug = market.event_slug
            row.close_time = market.close_time
            row.resolves_at = market.resolves_at
            row.resolved = market.resolved
        self._session.commit()
        self._session.refresh(row)
        return _from_db_market(row)

    def list(
        self, *, category: str | None = None, status: MarketStatus | None = None
    ) -> list[Market]:
        statement = select(models.Market)
        if category is not None:
            statement = statement.where(models.Market.category == category)
        if status is MarketStatus.RESOLVED:
            statement = statement.where(col(models.Market.resolved).is_(True))
        elif status is MarketStatus.OPEN:
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

    def latest_for_market(self, market_id: str) -> MarketSnapshot | None:
        row = self._session.exec(
            select(models.MarketSnapshot)
            .where(models.MarketSnapshot.market_id == uuid.UUID(market_id))
            .order_by(col(models.MarketSnapshot.ts).desc())
        ).first()
        return _from_db_snapshot(row) if row is not None else None

    def get(self, snapshot_id: str) -> MarketSnapshot | None:
        row = self._session.get(models.MarketSnapshot, uuid.UUID(snapshot_id))
        return _from_db_snapshot(row) if row is not None else None

    def latest_for_market_asof(self, market_id: str, *, asof: datetime) -> MarketSnapshot | None:
        # Point-in-time: the freshest snapshot NOT after asof — the price as the trader saw it.
        row = self._session.exec(
            select(models.MarketSnapshot)
            .where(models.MarketSnapshot.market_id == uuid.UUID(market_id))
            .where(col(models.MarketSnapshot.ts) <= asof)
            .order_by(col(models.MarketSnapshot.ts).desc())
        ).first()
        return _from_db_snapshot(row) if row is not None else None


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
        p_model_calibrated=estimate.p_model_calibrated,
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
        p_model_calibrated=row.p_model_calibrated,
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

    def exists_for_snapshot(self, snapshot_id: str) -> bool:
        row = self._session.exec(
            select(models.Estimate.id).where(
                col(models.Estimate.snapshot_id) == uuid.UUID(snapshot_id)
            )
        ).first()
        return row is not None

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


class RecalibrationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, category: str, cmap: CalibrationMap, n_fit: int) -> None:
        for row in self._active_rows(category):
            row.active = False
            self._session.add(row)
        self._session.flush()  # deactivate before inserting (partial unique index on active)
        self._session.add(
            models.RecalibrationModel(
                category=category,
                method=cmap.method,
                params=dict(cmap.params),
                n_fit=n_fit,
                active=True,
            )
        )
        self._session.commit()

    def active_for_category(self, category: str) -> tuple[CalibrationMap, int] | None:
        row = next(iter(self._active_rows(category)), None)
        if row is None:
            return None
        return CalibrationMap(method=as_method(row.method), params=row.params), row.n_fit

    def _active_rows(self, category: str) -> Sequence[models.RecalibrationModel]:
        return self._session.exec(
            select(models.RecalibrationModel)
            .where(
                models.RecalibrationModel.category == category,
                col(models.RecalibrationModel.active).is_(True),
            )
            .order_by(col(models.RecalibrationModel.fitted_at).desc())
        ).all()


@dataclass(frozen=True, slots=True)
class ResolutionOutcome:
    outcome: int
    resolved_at: datetime
    ambiguous: bool


class ResolutionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        market_id: str,
        outcome: int,
        *,
        at: datetime,
        ambiguous: bool = False,
        source: str | None = None,
    ) -> str:
        row_id = uuid.UUID(deterministic_id("resolution", market_id))
        existing = self._session.get(models.Resolution, row_id)
        if existing is not None:
            return str(existing.id)
        row = models.Resolution(
            id=row_id,
            market_id=uuid.UUID(market_id),
            outcome=outcome,
            resolved_at=at,
            source=source,
            ambiguous=ambiguous,
        )
        self._session.add(row)
        market = self._session.get(models.Market, uuid.UUID(market_id))
        if market is not None:
            market.resolved = True
            self._session.add(market)
        self._session.commit()
        self._session.refresh(row)
        return str(row.id)

    def exists(self, market_id: str) -> bool:
        row_id = uuid.UUID(deterministic_id("resolution", market_id))
        return self._session.get(models.Resolution, row_id) is not None

    def get_for_market(self, market_id: str) -> ResolutionOutcome | None:
        row = self._session.get(
            models.Resolution, uuid.UUID(deterministic_id("resolution", market_id))
        )
        if row is None:
            return None
        return ResolutionOutcome(
            outcome=row.outcome, resolved_at=row.resolved_at, ambiguous=row.ambiguous
        )

    def invalid_counts_by_category(self) -> dict[str, int]:
        rows = self._session.exec(
            select(models.Market.category, func.count())
            .join(models.Resolution, col(models.Resolution.market_id) == col(models.Market.id))
            .where(col(models.Resolution.ambiguous).is_(True))
            .group_by(col(models.Market.category))
        ).all()
        return dict(rows)

    def dispute_counts(self, category: str, *, since: datetime, asof: datetime) -> tuple[int, int]:
        total, invalid = self._session.exec(
            select(
                func.count(),
                func.count().filter(col(models.Resolution.ambiguous).is_(True)),
            )
            .select_from(models.Resolution)
            .join(models.Market, col(models.Resolution.market_id) == col(models.Market.id))
            .where(
                models.Market.category == category,
                col(models.Resolution.resolved_at) >= since,
                col(models.Resolution.resolved_at) < asof,
            )
        ).one()
        return invalid, total


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


# Calibration-mode candidates are ranked/filtered (scoring.rank_radar) after the SQL window; the
# repository returns this bounded candidate set as raw domain rows, no scoring on the db layer.
_RADAR_WINDOW_CAP = 500


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

    def radar(
        self, *, asof: datetime, max_days_to_resolve: int | None = None
    ) -> list[tuple[Score, Estimate, Market, float]]:
        """Bounded candidate set as raw domain rows (Score, Estimate, Market, yes_price).

        Ranking, per-row metrics and win filters live in scoring.rank_radar — db stays scoring-free.
        """
        latest_per_market = (
            select(models.Score.id)
            .join(models.Estimate, col(models.Score.estimate_id) == col(models.Estimate.id))
            .distinct(col(models.Estimate.market_id))
            .order_by(
                col(models.Estimate.market_id),
                col(models.Score.created_at).desc(),
                col(models.Score.id).desc(),
            )
        )
        # Entry gate (§A.1): only surface markets we can still enter — trading open (or unknown)
        # and not resolved. A closed market has no entry; ranking it would be misleading.
        query = (
            select(models.Score, models.Estimate, models.Market, models.MarketSnapshot.yes_price)
            .join(models.Estimate, col(models.Score.estimate_id) == col(models.Estimate.id))
            .join(models.Market, col(models.Estimate.market_id) == col(models.Market.id))
            .join(
                models.MarketSnapshot,
                col(models.Estimate.snapshot_id) == col(models.MarketSnapshot.id),
            )
            .where(col(models.Score.id).in_(latest_per_market))
            .where(col(models.Market.resolved).is_(False))
            .where(
                or_(
                    col(models.Market.close_time).is_(None),
                    col(models.Market.close_time) >= asof,
                )
            )
        )
        # The market resolves when UMA settles (resolves_at); fall back to trading-close when Gamma
        # gives no UMA date. Proximity to *resolution* is what drives calibration feedback speed.
        resolves_at = func.coalesce(col(models.Market.resolves_at), col(models.Market.close_time))
        windowed = max_days_to_resolve is not None
        if max_days_to_resolve is not None:
            cutoff = asof + timedelta(days=max_days_to_resolve)
            query = query.where(resolves_at >= asof).where(resolves_at <= cutoff)
        # Fetch a bounded candidate set (soonest-resolution when windowed, else best-score); the
        # caller ranks and filters it.
        order = resolves_at if windowed else col(models.Score.opportunity_score).desc()
        fetched = self._session.exec(query.order_by(order).limit(_RADAR_WINDOW_CAP)).all()
        return [
            (_from_db_score(score), _from_db_estimate(est), _from_db_market(market), q)
            for score, est, market, q in fetched
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


def _risk_decision_from_payload(payload: dict[str, Any]) -> RiskDecision:
    return RiskDecision(
        approved_size=float(payload["approved_size"]),
        vetoed=bool(payload["vetoed"]),
        reasons=tuple(RiskReason(reason) for reason in payload["reasons"]),
        paper_only=bool(payload["paper_only"]),
    )


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

    def latest_risk_decision(self, market_id: str, estimate_id: str) -> RiskDecision | None:
        rows = self._session.exec(
            select(models.AuditEvent)
            .where(models.AuditEvent.market_id == uuid.UUID(market_id))
            .where(models.AuditEvent.kind == AuditKind.RISK_DECISION)
            .order_by(col(models.AuditEvent.ts).desc(), col(models.AuditEvent.id).desc())
        ).all()
        for row in rows:
            if row.payload.get("estimate_id") == estimate_id:
                return _risk_decision_from_payload(row.payload)
        return None


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

    def by_category(self) -> dict[str, float]:
        statement = (
            select(models.Market.category, func.sum(models.CostEvent.cost_usd))
            .select_from(models.CostEvent)
            .join(models.Market, col(models.CostEvent.market_id) == col(models.Market.id))
            .group_by(col(models.Market.category))
        )
        return {category: float(total) for category, total in self._session.exec(statement).all()}

    def cost_per_scored_opportunity(self) -> float:
        scored = self._session.exec(select(func.count()).select_from(models.Score)).one()
        return self.total() / scored if scored else 0.0

    def by_day(self) -> dict[str, float]:
        day = func.date(col(models.CostEvent.ts))
        statement = select(day, func.sum(models.CostEvent.cost_usd)).group_by(day).order_by(day)
        return {str(date): float(total) for date, total in self._session.exec(statement).all()}


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


class CohortMembershipRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(self, trade_id: str, cohorts: Iterable[Cohort], *, asof: datetime) -> None:
        existing = self.cohorts_of(trade_id)
        added = False
        for cohort in cohorts:
            if cohort in existing:
                continue
            self._session.add(
                models.CohortMembership(
                    id=uuid.uuid4(),
                    paper_trade_id=uuid.UUID(trade_id),
                    cohort=cohort.value,
                    asof=asof,
                )
            )
            added = True
        if added:
            self._session.commit()

    def cohorts_of(self, trade_id: str) -> set[Cohort]:
        rows = self._session.exec(
            select(models.CohortMembership.cohort).where(
                models.CohortMembership.paper_trade_id == uuid.UUID(trade_id)
            )
        ).all()
        return {Cohort(row) for row in rows}


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


def _to_db_venue_quote(quote: VenueQuote) -> models.VenueQuote:
    return models.VenueQuote(
        id=uuid.UUID(quote.id),
        market_id=uuid.UUID(quote.market_id),
        venue=quote.venue.value,
        external_ref=quote.external_ref,
        ts=quote.ts,
        yes_price=quote.yes_price,
        match_confidence=quote.match_confidence,
    )


def _from_db_venue_quote(row: models.VenueQuote) -> VenueQuote:
    return VenueQuote(
        id=str(row.id),
        market_id=str(row.market_id),
        venue=Venue(row.venue),
        external_ref=row.external_ref,
        ts=row.ts,
        yes_price=row.yes_price,
        match_confidence=row.match_confidence,
    )


class VenueQuoteRepository:
    """Append-only, point-in-time store of matched cross-venue quotes (§4.10)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, quote: VenueQuote) -> VenueQuote:
        row = _to_db_venue_quote(quote)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _from_db_venue_quote(row)

    def retrieve(self, market_id: str, asof: datetime) -> list[VenueQuote]:
        rows = self._session.exec(
            select(models.VenueQuote)
            .where(models.VenueQuote.market_id == uuid.UUID(market_id))
            .where(col(models.VenueQuote.ts) <= asof)
            .order_by(col(models.VenueQuote.ts))
        ).all()
        return [_from_db_venue_quote(row) for row in rows]

    def retrieve_for_markets(
        self, asof_by_market: dict[str, datetime]
    ) -> dict[str, list[VenueQuote]]:
        """Batch fetch quotes per market, each filtered to its own asof in SQL (point-in-time)."""
        if not asof_by_market:
            return {}
        conditions = [
            and_(
                col(models.VenueQuote.market_id) == uuid.UUID(mid),
                col(models.VenueQuote.ts) <= asof,
            )
            for mid, asof in asof_by_market.items()
        ]
        rows = self._session.exec(
            select(models.VenueQuote).where(or_(*conditions)).order_by(col(models.VenueQuote.ts))
        ).all()
        result: dict[str, list[VenueQuote]] = {}
        for row in rows:
            result.setdefault(str(row.market_id), []).append(_from_db_venue_quote(row))
        return result


def _from_db_postmortem(row: models.Postmortem) -> Postmortem:
    return Postmortem(
        id=str(row.id),
        market_id=str(row.market_id),
        category=row.category,
        failure_class=FailureClass(row.failure_class),
        brier=row.brier,
        rationale=row.rationale,
        estimate_id=str(row.estimate_id) if row.estimate_id is not None else None,
        resolution_id=str(row.resolution_id) if row.resolution_id is not None else None,
        evidence_ids=[str(evidence_id) for evidence_id in row.evidence_ids],
    )


class PostmortemRepository:
    """Append-only store of attributed failures (§5.5)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, postmortem: Postmortem) -> Postmortem:
        row = models.Postmortem(
            id=uuid.UUID(postmortem.id),
            market_id=uuid.UUID(postmortem.market_id),
            category=postmortem.category,
            estimate_id=(
                uuid.UUID(postmortem.estimate_id) if postmortem.estimate_id is not None else None
            ),
            resolution_id=(
                uuid.UUID(postmortem.resolution_id)
                if postmortem.resolution_id is not None
                else None
            ),
            failure_class=postmortem.failure_class.value,
            brier=postmortem.brier,
            rationale=postmortem.rationale,
            evidence_ids=[uuid.UUID(evidence_id) for evidence_id in postmortem.evidence_ids],
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _from_db_postmortem(row)

    def list_for_market(self, market_id: str) -> list[Postmortem]:
        rows = self._session.exec(
            select(models.Postmortem).where(models.Postmortem.market_id == uuid.UUID(market_id))
        ).all()
        return [_from_db_postmortem(row) for row in rows]


def _from_db_candidate(row: models.ImprovementCandidate) -> ImprovementCandidate:
    return ImprovementCandidate(
        id=str(row.id),
        postmortem_id=str(row.postmortem_id),
        kind=ImprovementAction(row.kind),
        status=CandidateStatus(row.status),
        rationale=row.rationale,
        requires_review=row.requires_review,
        brier_per_dollar=row.brier_per_dollar,
        n=row.n,
        human_approved=row.human_approved,
    )


class ImprovementCandidateRepository:
    """The only mutable §5.5 table: candidates move proposed → validating → adopted | rejected."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, candidate: ImprovementCandidate) -> ImprovementCandidate:
        row = models.ImprovementCandidate(
            id=uuid.UUID(candidate.id),
            postmortem_id=uuid.UUID(candidate.postmortem_id),
            kind=candidate.kind.value,
            status=candidate.status.value,
            rationale=candidate.rationale,
            requires_review=candidate.requires_review,
            brier_per_dollar=candidate.brier_per_dollar,
            n=candidate.n,
            human_approved=candidate.human_approved,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _from_db_candidate(row)

    def get(self, candidate_id: str) -> ImprovementCandidate | None:
        row = self._session.get(models.ImprovementCandidate, uuid.UUID(candidate_id))
        return _from_db_candidate(row) if row is not None else None

    def list_by_status(self, status: CandidateStatus) -> list[ImprovementCandidate]:
        rows = self._session.exec(
            select(models.ImprovementCandidate).where(
                models.ImprovementCandidate.status == status.value
            )
        ).all()
        return [_from_db_candidate(row) for row in rows]

    def set_status(
        self,
        candidate_id: str,
        status: CandidateStatus,
        *,
        brier_per_dollar: float | None = None,
        n: int | None = None,
        human_approved: bool | None = None,
    ) -> ImprovementCandidate:
        row = self._session.get(models.ImprovementCandidate, uuid.UUID(candidate_id))
        if row is None:
            raise KeyError(candidate_id)
        row.status = status.value
        row.updated_at = datetime.now(UTC)
        if brier_per_dollar is not None:
            row.brier_per_dollar = brier_per_dollar
        if n is not None:
            row.n = n
        if human_approved is not None:
            row.human_approved = human_approved
        self._session.commit()
        self._session.refresh(row)
        return _from_db_candidate(row)


def persist_crawl(session: Session, pairs: Sequence[tuple[Market, MarketSnapshot]]) -> None:
    markets = MarketRepository(session)
    snapshots = SnapshotRepository(session)
    for market, snapshot in pairs:
        markets.upsert(market)
        snapshots.append(snapshot)
