"""Human review gate (§1.3, §2.1, §4.4): the only path from a scored opportunity to paper.

No autonomous capital allocation — a paper trade exists only after explicit human approval, in paper
mode, with the approval and the open recorded in audit_events. Reject is terminal. The trade params
(side, size) come from the reviewed, risk-approved opportunity; the ledger fills at the observed
snapshot price.
"""

import uuid
from datetime import datetime

from sqlmodel import Session

from pythia.common import AuditEvent, AuditKind, PaperLedgerPort, PaperTrade, Side, TradeStatus
from pythia.config import Settings, ensure_paper_mode
from pythia.db.repositories import AuditRepository, EstimateRepository, ScoreRepository

_PLACEHOLDER_ENTRY = 0.0


class UnknownEstimateError(LookupError):
    """No estimate matches the id under review."""


class NotScoredError(RuntimeError):
    """Only a scored opportunity can be reviewed; the estimate has no score."""


class NotRiskEvaluatedError(RuntimeError):
    """No risk decision is recorded; the opportunity never passed the deterministic gate (AC-5)."""


class VetoedOpportunityError(RuntimeError):
    """The deterministic risk gate vetoed this opportunity; it cannot reach paper (AC-5)."""


def _trade_id(estimate_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"paper-trade|{estimate_id}"))


class ReviewGate:
    def __init__(self, session: Session, ledger: PaperLedgerPort, settings: Settings) -> None:
        self._ledger = ledger
        self._settings = settings
        self._estimates = EstimateRepository(session)
        self._scores = ScoreRepository(session)
        self._audit = AuditRepository(session)

    def approve(
        self,
        estimate_id: str,
        *,
        side: Side,
        size: float,
        at: datetime,
        reviewer: str | None = None,
    ) -> PaperTrade:
        ensure_paper_mode(self._settings)
        estimate = self._estimates.get(estimate_id)
        if estimate is None:
            raise UnknownEstimateError(estimate_id)
        if self._scores.get_by_estimate(estimate_id) is None:
            raise NotScoredError(estimate_id)

        decision = self._audit.latest_risk_decision(estimate.market_id, estimate_id)
        if decision is None:
            raise NotRiskEvaluatedError(estimate_id)
        if decision.vetoed:
            raise VetoedOpportunityError(estimate_id)
        approved_size = min(size, decision.approved_size)

        trade = self._ledger.open(
            PaperTrade(
                id=_trade_id(estimate_id),
                market_id=estimate.market_id,
                estimate_id=estimate_id,
                side=side,
                entry_price=_PLACEHOLDER_ENTRY,
                size=approved_size,
                status=TradeStatus.OPEN,
                opened_at=at,
            )
        )
        self._audit.append(
            AuditEvent(
                kind=AuditKind.APPROVAL,
                market_id=estimate.market_id,
                payload={
                    "estimate_id": estimate_id,
                    "reviewer": reviewer,
                    "side": side.value,
                    "requested_size": size,
                    "size": approved_size,
                },
            )
        )
        self._audit.append(
            AuditEvent(
                kind=AuditKind.PAPER_OPEN,
                market_id=estimate.market_id,
                payload={
                    "estimate_id": estimate_id,
                    "trade_id": trade.id,
                    "side": trade.side.value,
                    "entry_price": trade.entry_price,
                    "size": trade.size,
                },
            )
        )
        return trade

    def reject(self, estimate_id: str, *, reviewer: str | None = None) -> None:
        estimate = self._estimates.get(estimate_id)
        if estimate is None:
            raise UnknownEstimateError(estimate_id)
        self._audit.append(
            AuditEvent(
                kind=AuditKind.REJECTION,
                market_id=estimate.market_id,
                payload={"estimate_id": estimate_id, "reviewer": reviewer},
            )
        )
