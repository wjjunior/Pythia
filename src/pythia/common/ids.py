"""Deterministic, content-addressed IDs: stable across runs so persistence stays idempotent.

A uuid5 over the joined parts means the same logical entity (an estimate for a snapshot, an evidence
chunk, a post-mortem for an estimate) always hashes to the same id — re-running the pipeline upserts
rather than duplicating.
"""

import uuid


def deterministic_id(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(parts)))
