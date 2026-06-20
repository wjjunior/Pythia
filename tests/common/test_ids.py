"""Tests for the shared deterministic-id helper."""

import uuid

from pythia.common.ids import deterministic_id


def test_is_stable_across_calls() -> None:
    assert deterministic_id("estimate", "snap-1") == deterministic_id("estimate", "snap-1")


def test_distinct_parts_yield_distinct_ids() -> None:
    assert deterministic_id("estimate", "snap-1") != deterministic_id("estimate", "snap-2")
    assert deterministic_id("a", "b") != deterministic_id("ab")


def test_is_a_valid_uuid_string() -> None:
    value = deterministic_id("postmortem", "est-1")
    assert str(uuid.UUID(value)) == value
