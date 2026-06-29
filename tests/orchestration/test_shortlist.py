"""Pure tests for the rotating discovery-shortlist cursor (PYT-165)."""

from pythia.common import Market
from pythia.orchestration.runner import rotate_shortlist


def _market(mid: str) -> Market:
    return Market(id=mid, external_id=mid, question="q", category="c", resolution_rule="r")


_MARKETS = [_market(c) for c in ("a", "b", "c", "d", "e")]


def test_first_slice_starts_at_the_beginning_and_sets_the_cursor() -> None:
    chosen, cursor = rotate_shortlist(_MARKETS, after_id=None, size=2)
    assert [m.id for m in chosen] == ["a", "b"]
    assert cursor == "b"


def test_next_slice_continues_after_the_cursor() -> None:
    chosen, cursor = rotate_shortlist(_MARKETS, after_id="b", size=2)
    assert [m.id for m in chosen] == ["c", "d"]
    assert cursor == "d"


def test_slice_wraps_around_at_the_end() -> None:
    chosen, cursor = rotate_shortlist(_MARKETS, after_id="d", size=2)
    assert [m.id for m in chosen] == ["e", "a"]  # wrapped back to the start
    assert cursor == "a"


def test_three_ticks_cover_every_market() -> None:
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(3):  # ceil(5/2) = 3 ticks
        chosen, cursor = rotate_shortlist(_MARKETS, after_id=cursor, size=2)
        seen.extend(m.id for m in chosen)
    assert set(seen) == {"a", "b", "c", "d", "e"}  # the union covers the whole open set


def test_size_at_or_above_count_returns_all_markets() -> None:
    chosen, cursor = rotate_shortlist(_MARKETS, after_id=None, size=10)
    assert [m.id for m in chosen] == ["a", "b", "c", "d", "e"]
    assert cursor == "e"


def test_empty_market_set_keeps_the_cursor() -> None:
    chosen, cursor = rotate_shortlist([], after_id="x", size=2)
    assert chosen == [] and cursor == "x"


def test_an_unknown_cursor_restarts_from_the_beginning() -> None:
    # The cursor's market resolved out of the open set — restart from the front, never crash.
    chosen, _ = rotate_shortlist(_MARKETS, after_id="zzz", size=2)
    assert [m.id for m in chosen] == ["a", "b"]
