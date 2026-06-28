"""Per-market review/paper state for the ops views (PYT-153).

A market's `ReviewStatus` is derived in one batch from the paper ledger and the review-gate audit
trail, so the radar and the smart-money feed can flag what has already been acted on. Precedence:
an open position dominates a resolved one dominates a rejection.
"""

import uuid
from collections.abc import Iterable

from sqlmodel import Session, col, select

from pythia.common import AuditKind, ReviewStatus, TradeStatus
from pythia.db import models


def review_status_by_market(session: Session, market_ids: Iterable[str]) -> dict[str, ReviewStatus]:
    ids = list(dict.fromkeys(market_ids))
    status = dict.fromkeys(ids, ReviewStatus.NONE)
    if not ids:
        return status
    uuids = [uuid.UUID(market_id) for market_id in ids]

    open_markets: set[str] = set()
    resolved_markets: set[str] = set()
    for trade_market, trade_status in session.exec(
        select(models.PaperTrade.market_id, models.PaperTrade.status).where(
            col(models.PaperTrade.market_id).in_(uuids)
        )
    ).all():
        bucket = open_markets if trade_status == TradeStatus.OPEN else resolved_markets
        bucket.add(str(trade_market))

    rejected_markets = {
        str(rejected)
        for rejected in session.exec(
            select(models.AuditEvent.market_id)
            .where(col(models.AuditEvent.market_id).in_(uuids))
            .where(models.AuditEvent.kind == AuditKind.REJECTION)
        ).all()
        if rejected is not None
    }

    for market_id in ids:
        if market_id in open_markets:
            status[market_id] = ReviewStatus.OPEN
        elif market_id in resolved_markets:
            status[market_id] = ReviewStatus.RESOLVED
        elif market_id in rejected_markets:
            status[market_id] = ReviewStatus.REJECTED
    return status
