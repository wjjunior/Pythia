"""Tests for the shared deterministic-id helper."""

import uuid

from pythia.common import ArbKind
from pythia.common.ids import arb_identity_key, deterministic_id


def test_is_stable_across_calls() -> None:
    assert deterministic_id("estimate", "snap-1") == deterministic_id("estimate", "snap-1")


def test_distinct_parts_yield_distinct_ids() -> None:
    assert deterministic_id("estimate", "snap-1") != deterministic_id("estimate", "snap-2")
    assert deterministic_id("a", "b") != deterministic_id("ab")


def test_is_a_valid_uuid_string() -> None:
    value = deterministic_id("postmortem", "est-1")
    assert str(uuid.UUID(value)) == value


def test_arb_identity_key_is_order_independent_in_market_ids() -> None:
    a = arb_identity_key("cup", ArbKind.DUTCH_BOOK, ["m2", "m1", "m3"])
    b = arb_identity_key("cup", ArbKind.DUTCH_BOOK, ["m1", "m2", "m3"])
    assert a == b == "cup|dutch_book|m1|m2|m3"


def test_arb_identity_key_distinguishes_event_kind_and_market_set() -> None:
    base = arb_identity_key("cup", ArbKind.DUTCH_BOOK, ["m1", "m2"])
    assert base != arb_identity_key("cup2", ArbKind.DUTCH_BOOK, ["m1", "m2"])
    assert base != arb_identity_key("cup", ArbKind.LADDER_SPREAD, ["m1", "m2"])
    assert base != arb_identity_key("cup", ArbKind.DUTCH_BOOK, ["m1", "m3"])
