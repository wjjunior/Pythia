"""Deterministic, content-addressed IDs: stable across runs so persistence stays idempotent.

A uuid5 over the joined parts means the same logical entity (an estimate for a snapshot, an evidence
chunk, a post-mortem for an estimate) always hashes to the same id — re-running the pipeline upserts
rather than duplicating.
"""

import uuid
from collections.abc import Iterable

from pythia.common.types import ArbKind


def deterministic_id(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(parts)))


def arb_identity_key(event_slug: str, kind: ArbKind, market_ids: Iterable[str]) -> str:
    """Stable identity of an economic arb across passes: event, kind, and its sorted leg markets.

    Side is not part of the key — it is fixed by kind + threshold order (dutch book = all YES;
    ladder = low YES / high NO), so the ordered market-id set pins the legs and their sides."""
    return "|".join([event_slug, kind.value, *sorted(market_ids)])
