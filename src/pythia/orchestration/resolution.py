"""Resolution handling (§5 Event Resolution, §5.5, §8.4): record → audit → settle → post-mortem.

When a market resolves we persist the resolution, emit the RESOLUTION audit event, settle open paper
trades to PnL, then hand the resolution to the post-mortem loop. The loop only acts on statistical
surprise or category drift (§5.5) and never touches scoring/risk — it accumulates evidence and
proposes; the harness and the human decide.
"""

from collections.abc import Callable
from datetime import datetime

from sqlmodel import Session

from pythia.common import AuditEvent, AuditKind, PaperLedgerPort, Postmortem
from pythia.common.logging import get_logger
from pythia.config import Settings
from pythia.db.repositories import (
    AuditRepository,
    EstimateRepository,
    EvidenceRepository,
    MarketRepository,
    ResolutionRepository,
)
from pythia.eval.postmortem import AttributionPort
from pythia.eval.service import CalibrationService, PostmortemService

_log = get_logger(__name__)


class ResolutionService:
    """Wires a market resolution to the audit trail, the paper ledger and the post-mortem loop."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        ledger: PaperLedgerPort,
        *,
        attribution: AttributionPort | None = None,
        on_calibration_changed: Callable[[str], None] | None = None,
    ) -> None:
        self._resolutions = ResolutionRepository(session)
        self._markets = MarketRepository(session)
        self._estimates = EstimateRepository(session)
        self._evidence = EvidenceRepository(session)
        self._audit = AuditRepository(session)
        self._ledger = ledger
        self._calibration = CalibrationService(session, settings=settings)
        self._postmortem = PostmortemService(session, settings, attribution=attribution)
        self._on_calibration_changed = on_calibration_changed

    def record(
        self,
        market_id: str,
        outcome: int,
        *,
        at: datetime,
        ambiguous: bool = False,
        source: str | None = None,
    ) -> Postmortem | None:
        resolution_id = self._resolutions.record(
            market_id, outcome, at=at, ambiguous=ambiguous, source=source
        )
        self._audit.append(
            AuditEvent(
                kind=AuditKind.RESOLUTION,
                market_id=market_id,
                ts=at,
                payload={
                    "resolution_id": resolution_id,
                    "outcome": outcome,
                    "ambiguous": ambiguous,
                },
            )
        )
        self._ledger.resolve(market_id, outcome, at)

        market = self._markets.get(market_id)
        if market is not None and self._on_calibration_changed is not None:
            self._on_calibration_changed(market.category)

        estimate = self._estimates.latest_for_market(market_id)
        if market is None or estimate is None:
            return None

        if self._postmortem.detect_category_drift(
            self._calibration.category_brier_deltas(market.category)
        ):
            _log.warning("category_brier_drift", category=market.category)

        evidence = self._evidence.get_by_ids(estimate.evidence_ids)
        return self._postmortem.review_resolution(
            estimate,
            evidence,
            category=market.category,
            outcome=outcome,
            ambiguous=ambiguous,
            asof=at,
            resolution_id=resolution_id,
        )
