"""Risk-approved size per estimate for the radar approve action (PYT-172).

The deterministic risk gate records an ``approved_size`` per (market, estimate) in the audit trail.
The radar surfaces it so the operator approves a paper trade at the size the math already chose —
the human gate is approve/reject, never sizing. A vetoed or never-evaluated opportunity has no
positive size (``vetoed`` ⇒ ``approved_size == 0``), so 0.0 doubles as the "not approvable" signal.
"""

import uuid
from collections.abc import Iterable

from sqlmodel import Session, col, select

from pythia.common import AuditKind
from pythia.db import models


def suggested_size_by_estimate(
    session: Session, pairs: Iterable[tuple[str, str]]
) -> dict[str, float]:
    """Latest risk-approved size keyed by estimate id; 0.0 when vetoed or never risk-evaluated."""
    materialized = list(pairs)
    sizes = {estimate_id: 0.0 for _, estimate_id in materialized}
    if not materialized:
        return sizes
    market_uuids = [uuid.UUID(market_id) for market_id, _ in materialized]
    rows = session.exec(
        select(models.AuditEvent)
        .where(col(models.AuditEvent.market_id).in_(market_uuids))
        .where(models.AuditEvent.kind == AuditKind.RISK_DECISION)
        .order_by(col(models.AuditEvent.ts).desc(), col(models.AuditEvent.id).desc())
    ).all()
    seen: set[str] = set()  # first row per estimate is the latest (desc order) — even if it vetoes
    for row in rows:
        estimate_id = row.payload.get("estimate_id")
        if estimate_id in sizes and estimate_id not in seen:
            seen.add(estimate_id)
            sizes[estimate_id] = float(row.payload.get("approved_size", 0.0))
    return sizes
