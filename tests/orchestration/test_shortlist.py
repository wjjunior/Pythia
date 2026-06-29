"""Pure tests for the rotating discovery-shortlist cursor (PYT-165) + midband priority (PYT-167)."""

from collections.abc import Callable

from pythia.common import Market
from pythia.eval.cohorts import Cohort, CohortPolicy, cohorts_for
from pythia.orchestration.runner import rotate_shortlist
from pythia.scoring import price_band_priority


def _market(mid: str, *, category: str = "c") -> Market:
    return Market(id=mid, external_id=mid, question="q", category=category, resolution_rule="r")


_MARKETS = [_market(c) for c in ("a", "b", "c", "d", "e")]

_LO, _HI = 0.10, 0.85
# a,c,e midband; b,d near-certain tails
_PRICES = {"a": 0.50, "b": 0.97, "c": 0.30, "d": 0.02, "e": 0.60}


def _band_key(prices: dict[str, float]) -> Callable[[Market], tuple[int, str]]:
    return lambda m: (price_band_priority(prices[m.id], lo=_LO, hi=_HI), m.id)


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


# --- PYT-167: midband price-band priority within the rotation ---


def test_price_band_priority_ranks_midband_first() -> None:
    assert price_band_priority(0.50, lo=_LO, hi=_HI) == 0  # midband
    assert price_band_priority(_LO, lo=_LO, hi=_HI) == 0  # boundary inclusive
    assert price_band_priority(_HI, lo=_LO, hi=_HI) == 0  # boundary inclusive
    assert price_band_priority(0.05, lo=_LO, hi=_HI) == 1  # longshot tail
    assert price_band_priority(0.97, lo=_LO, hi=_HI) == 1  # near-certain tail


def test_midband_markets_are_estimated_before_the_tails() -> None:
    chosen, _ = rotate_shortlist(_MARKETS, after_id=None, size=3, key=_band_key(_PRICES))
    assert [m.id for m in chosen] == ["a", "c", "e"]  # all 3 midband first, no tail selected


def test_tails_are_still_covered_after_the_midband_not_excluded() -> None:
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(3):  # size 2 over 5 markets
        chosen, cursor = rotate_shortlist(_MARKETS, after_id=cursor, size=2, key=_band_key(_PRICES))
        seen.extend(m.id for m in chosen)
    assert {"b", "d"}.issubset(seen)  # the tails are reached, just later
    assert set(seen) == {"a", "b", "c", "d", "e"}  # full coverage, priority not exclusion


def test_category_is_not_a_selection_key() -> None:
    # Two midband markets, different categories; ordering is by (band, id) — category is irrelevant.
    markets = [_market("z_politics", category="politics"), _market("a_sports", category="sports")]
    prices = {"z_politics": 0.5, "a_sports": 0.5}
    chosen, _ = rotate_shortlist(markets, after_id=None, size=1, key=_band_key(prices))
    assert chosen[0].id == "a_sports"  # smaller id wins; the category never biases selection


def test_priority_band_matches_the_midband_cohort() -> None:
    # Selection and measurement agree: a market the shortlist prioritises is exactly one the MIDBAND
    # eval cohort measures — so the bias never diverges from what the stratified Brier reads.
    policy = CohortPolicy(max_horizon_days=7.0, price_min=_LO, price_max=_HI)
    for price in (0.05, 0.30, 0.50, 0.85, 0.97):
        midband_priority = price_band_priority(price, lo=_LO, hi=_HI) == 0
        in_cohort = Cohort.MIDBAND in cohorts_for(price=price, horizon_days=None, policy=policy)
        assert midband_priority == in_cohort
