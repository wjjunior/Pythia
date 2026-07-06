from pythia.common import ArbKind
from pythia.scoring.events import arb_identity_key


def test_identity_key_is_order_independent_in_market_ids() -> None:
    a = arb_identity_key("cup", ArbKind.DUTCH_BOOK, ["m2", "m1", "m3"])
    b = arb_identity_key("cup", ArbKind.DUTCH_BOOK, ["m1", "m2", "m3"])
    assert a == b == "cup|dutch_book|m1|m2|m3"


def test_identity_key_distinguishes_event_kind_and_market_set() -> None:
    base = arb_identity_key("cup", ArbKind.DUTCH_BOOK, ["m1", "m2"])
    assert base != arb_identity_key("cup2", ArbKind.DUTCH_BOOK, ["m1", "m2"])
    assert base != arb_identity_key("cup", ArbKind.LADDER_SPREAD, ["m1", "m2"])
    assert base != arb_identity_key("cup", ArbKind.DUTCH_BOOK, ["m1", "m3"])
