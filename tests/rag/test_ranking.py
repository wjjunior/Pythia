"""Tests for the pure retrieval ranking helpers."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pythia.rag import freshness, rank_score


def test_freshness_is_one_at_zero_age() -> None:
    assert freshness(0.0, halflife_seconds=86400.0) == 1.0


def test_freshness_halves_at_one_halflife() -> None:
    assert freshness(86400.0, halflife_seconds=86400.0) == pytest.approx(0.5)


@given(
    st.floats(min_value=0.0, max_value=1e8, allow_nan=False, allow_infinity=False),
    st.floats(min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False),
)
def test_freshness_in_unit_interval_and_non_increasing(age: float, halflife: float) -> None:
    current = freshness(age, halflife_seconds=halflife)
    assert 0.0 <= current <= 1.0
    assert freshness(age + 1.0, halflife_seconds=halflife) <= current + 1e-12


def test_rank_score_is_the_product_of_its_factors() -> None:
    assert rank_score(0.8, 0.5, 0.5) == pytest.approx(0.2)
