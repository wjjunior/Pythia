"""Repositories for the v2 smart-money tables (§4.4, §4.6).

Point-in-time everywhere: scorecard and entry reads are filtered by ``asof`` and never return rows
after it (§4.6, §3.5). ``trader_scorecards`` and ``roster_events`` are append-only (DB-enforced);
their repos expose no update path. ``trader_entries`` insert is idempotent on
``(trader_id, external_market, ts)`` so re-polling never duplicates an entry (AC-S1).
"""

import uuid
from datetime import datetime

from sqlmodel import Session, col, select

from pythia.common import (
    CandidateSignal,
    CandidateSignalStatus,
    CandidateSource,
    RosterEvent,
    TrackedTrader,
    TraderEntry,
    TraderScorecard,
    TraderState,
)
from pythia.db import models


def _to_db_trader(t: TrackedTrader) -> models.TrackedTrader:
    return models.TrackedTrader(
        id=uuid.UUID(t.id),
        wallet=t.wallet,
        state=t.state.value,
        added_at=t.added_at,
        state_since=t.state_since,
        note=t.note,
    )


def _from_db_trader(row: models.TrackedTrader) -> TrackedTrader:
    return TrackedTrader(
        id=str(row.id),
        wallet=row.wallet,
        state=TraderState(row.state),
        added_at=row.added_at,
        state_since=row.state_since,
        note=row.note,
    )


def _from_db_scorecard(row: models.TraderScorecard) -> TraderScorecard:
    return TraderScorecard(
        id=str(row.id),
        trader_id=str(row.trader_id),
        asof=row.asof,
        n_resolved=row.n_resolved,
        market_class_ok=row.market_class_ok,
        trader_score=row.trader_score,
        roi=row.roi,
        risk_adj_edge=row.risk_adj_edge,
        sortino=row.sortino,
        skew=row.skew,
        max_drawdown=row.max_drawdown,
        agree_rate=row.agree_rate,
        agreed_bpd=row.agreed_bpd,
        raw=row.raw,
    )


def _from_db_entry(row: models.TraderEntry) -> TraderEntry:
    return TraderEntry(
        id=str(row.id),
        trader_id=str(row.trader_id),
        external_market=row.external_market,
        side=row.side,
        ts=row.ts,
        is_increase=row.is_increase,
        market_id=str(row.market_id) if row.market_id else None,
        size_usd=row.size_usd,
        entry_price=row.entry_price,
    )


def _from_db_candidate(row: models.CandidateSignal) -> CandidateSignal:
    return CandidateSignal(
        id=str(row.id),
        asof=row.asof,
        trader_ids=[str(tid) for tid in row.trader_ids],
        source=CandidateSource(row.source),
        status=CandidateSignalStatus(row.status),
        created_at=row.created_at,
        market_id=str(row.market_id) if row.market_id else None,
    )


def _from_db_event(row: models.RosterEvent) -> RosterEvent:
    return RosterEvent(
        id=str(row.id),
        trader_id=str(row.trader_id),
        to_state=TraderState(row.to_state),
        reason=row.reason,
        created_at=row.created_at,
        from_state=TraderState(row.from_state) if row.from_state else None,
        metrics=row.metrics,
    )


class TrackedTraderRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, trader: TrackedTrader) -> TrackedTrader:
        row = _to_db_trader(trader)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _from_db_trader(row)

    def get(self, trader_id: str) -> TrackedTrader | None:
        row = self._session.get(models.TrackedTrader, uuid.UUID(trader_id))
        return _from_db_trader(row) if row is not None else None

    def get_by_wallet(self, wallet: str) -> TrackedTrader | None:
        row = self._session.exec(
            select(models.TrackedTrader).where(models.TrackedTrader.wallet == wallet)
        ).one_or_none()
        return _from_db_trader(row) if row is not None else None

    def list(self, *, state: TraderState | None = None) -> list[TrackedTrader]:
        statement = select(models.TrackedTrader)
        if state is not None:
            statement = statement.where(models.TrackedTrader.state == state.value)
        rows = self._session.exec(statement.order_by(col(models.TrackedTrader.added_at))).all()
        return [_from_db_trader(row) for row in rows]

    def set_state(self, trader_id: str, state: TraderState, *, at: datetime) -> TrackedTrader:
        row = self._session.get(models.TrackedTrader, uuid.UUID(trader_id))
        if row is None:
            raise KeyError(trader_id)
        row.state = state.value
        row.state_since = at
        self._session.commit()
        self._session.refresh(row)
        return _from_db_trader(row)


class TraderScorecardRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, card: TraderScorecard) -> TraderScorecard:  # append-only
        row = models.TraderScorecard(
            id=uuid.UUID(card.id),
            trader_id=uuid.UUID(card.trader_id),
            asof=card.asof,
            n_resolved=card.n_resolved,
            market_class_ok=card.market_class_ok,
            trader_score=card.trader_score,
            roi=card.roi,
            risk_adj_edge=card.risk_adj_edge,
            sortino=card.sortino,
            skew=card.skew,
            max_drawdown=card.max_drawdown,
            agree_rate=card.agree_rate,
            agreed_bpd=card.agreed_bpd,
            raw=card.raw,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _from_db_scorecard(row)

    def latest(self, trader_id: str, asof: datetime) -> TraderScorecard | None:
        # Point-in-time: the freshest scorecard NOT after asof (§4.6 — no lookahead).
        row = self._session.exec(
            select(models.TraderScorecard)
            .where(models.TraderScorecard.trader_id == uuid.UUID(trader_id))
            .where(col(models.TraderScorecard.asof) <= asof)
            .order_by(col(models.TraderScorecard.asof).desc())
        ).first()
        return _from_db_scorecard(row) if row is not None else None

    def latest_for_trader(self, trader_id: str) -> TraderScorecard | None:
        row = self._session.exec(
            select(models.TraderScorecard)
            .where(models.TraderScorecard.trader_id == uuid.UUID(trader_id))
            .order_by(col(models.TraderScorecard.asof).desc())
        ).first()
        return _from_db_scorecard(row) if row is not None else None


class TraderEntryRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _find(
        self, trader_id: str, external_market: str, ts: datetime
    ) -> models.TraderEntry | None:
        return self._session.exec(
            select(models.TraderEntry)
            .where(models.TraderEntry.trader_id == uuid.UUID(trader_id))
            .where(models.TraderEntry.external_market == external_market)
            .where(models.TraderEntry.ts == ts)
        ).one_or_none()

    def _insert(self, entry: TraderEntry) -> TraderEntry:
        row = models.TraderEntry(
            id=uuid.UUID(entry.id),
            trader_id=uuid.UUID(entry.trader_id),
            market_id=uuid.UUID(entry.market_id) if entry.market_id else None,
            external_market=entry.external_market,
            side=entry.side,
            size_usd=entry.size_usd,
            entry_price=entry.entry_price,
            ts=entry.ts,
            is_increase=entry.is_increase,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _from_db_entry(row)

    def add(self, entry: TraderEntry) -> TraderEntry:
        # Idempotent on (trader_id, external_market, ts): re-polling never duplicates (AC-S1).
        existing = self._find(entry.trader_id, entry.external_market, entry.ts)
        return _from_db_entry(existing) if existing is not None else self._insert(entry)

    def add_new(self, entry: TraderEntry) -> TraderEntry | None:
        # Like add, but returns None when the entry was already seen — lets the detector trigger a
        # candidate only on a genuinely new entry, never on a re-poll (AC-S1).
        if self._find(entry.trader_id, entry.external_market, entry.ts) is not None:
            return None
        return self._insert(entry)

    def list_for_trader(self, trader_id: str, *, asof: datetime) -> list[TraderEntry]:
        # Point-in-time: never return an entry observed after asof (§4.6).
        rows = self._session.exec(
            select(models.TraderEntry)
            .where(models.TraderEntry.trader_id == uuid.UUID(trader_id))
            .where(col(models.TraderEntry.ts) <= asof)
            .order_by(col(models.TraderEntry.ts))
        ).all()
        return [_from_db_entry(row) for row in rows]

    def last_for_trader(self, trader_id: str) -> TraderEntry | None:
        row = self._session.exec(
            select(models.TraderEntry)
            .where(models.TraderEntry.trader_id == uuid.UUID(trader_id))
            .order_by(col(models.TraderEntry.ts).desc())
        ).first()
        return _from_db_entry(row) if row is not None else None


class CandidateSignalRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, signal: CandidateSignal) -> CandidateSignal:
        row = models.CandidateSignal(
            id=uuid.UUID(signal.id),
            market_id=uuid.UUID(signal.market_id) if signal.market_id else None,
            asof=signal.asof,
            trader_ids=[uuid.UUID(tid) for tid in signal.trader_ids],
            source=signal.source.value,
            status=signal.status.value,
            created_at=signal.created_at,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _from_db_candidate(row)

    def get(self, signal_id: str) -> CandidateSignal | None:
        row = self._session.get(models.CandidateSignal, uuid.UUID(signal_id))
        return _from_db_candidate(row) if row is not None else None

    def list_by_status(self, status: CandidateSignalStatus) -> list[CandidateSignal]:
        rows = self._session.exec(
            select(models.CandidateSignal)
            .where(models.CandidateSignal.status == status.value)
            .order_by(col(models.CandidateSignal.asof))
        ).all()
        return [_from_db_candidate(row) for row in rows]

    def recent(self, limit: int) -> list[CandidateSignal]:
        rows = self._session.exec(
            select(models.CandidateSignal)
            .order_by(col(models.CandidateSignal.created_at).desc())
            .limit(limit)
        ).all()
        return [_from_db_candidate(row) for row in rows]

    def set_status(self, signal_id: str, status: CandidateSignalStatus) -> CandidateSignal:
        row = self._session.get(models.CandidateSignal, uuid.UUID(signal_id))
        if row is None:
            raise KeyError(signal_id)
        row.status = status.value
        self._session.commit()
        self._session.refresh(row)
        return _from_db_candidate(row)


class RosterEventRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: RosterEvent) -> RosterEvent:  # append-only
        row = models.RosterEvent(
            id=uuid.UUID(event.id),
            trader_id=uuid.UUID(event.trader_id),
            from_state=event.from_state.value if event.from_state else None,
            to_state=event.to_state.value,
            reason=event.reason,
            metrics=event.metrics,
            created_at=event.created_at,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _from_db_event(row)

    def list_for_trader(self, trader_id: str) -> list[RosterEvent]:
        rows = self._session.exec(
            select(models.RosterEvent)
            .where(models.RosterEvent.trader_id == uuid.UUID(trader_id))
            .order_by(col(models.RosterEvent.created_at))
        ).all()
        return [_from_db_event(row) for row in rows]

    def latest_for_trader(self, trader_id: str) -> RosterEvent | None:
        row = self._session.exec(
            select(models.RosterEvent)
            .where(models.RosterEvent.trader_id == uuid.UUID(trader_id))
            .order_by(col(models.RosterEvent.created_at).desc())
        ).first()
        return _from_db_event(row) if row is not None else None

    def recent(self, limit: int) -> list[RosterEvent]:
        rows = self._session.exec(
            select(models.RosterEvent)
            .order_by(col(models.RosterEvent.created_at).desc())
            .limit(limit)
        ).all()
        return [_from_db_event(row) for row in rows]
