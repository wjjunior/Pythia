"""Property + example tests for the pure forecast aggregator (§5.4)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pythia.agents.aggregation import AggregatedForecast, aggregate
from pythia.common import Estimate


def _member(
    p: float, *, confidence: float = 0.5, lower: float = 0.0, upper: float = 1.0
) -> Estimate:
    return Estimate(
        id="e",
        market_id="m",
        snapshot_id="s",
        p_model=p,
        confidence=confidence,
        lower=lower,
        upper=upper,
        rationale="r",
        evidence_ids=[],
        model_name="x/model",
    )


def test_single_member_aggregates_to_itself() -> None:
    result = aggregate([_member(0.7, confidence=0.6, lower=0.5, upper=0.8)], "median")
    assert result == AggregatedForecast(p_model=0.7, confidence=0.6, lower=0.5, upper=0.8)


def test_median_takes_the_middle_value() -> None:
    members = [_member(0.2), _member(0.9), _member(0.5)]
    assert aggregate(members, "median").p_model == 0.5


def test_weighted_with_equal_weights_is_the_mean() -> None:
    members = [_member(0.2), _member(0.4), _member(0.9)]
    assert aggregate(members, "weighted").p_model == 0.5


def test_weighted_respects_member_weights() -> None:
    members = [_member(0.2), _member(0.8)]
    assert aggregate(members, "weighted", weights=[3.0, 1.0]).p_model == pytest.approx(0.35)


def test_band_is_the_median_of_member_bounds() -> None:
    members = [
        _member(0.5, lower=0.1, upper=0.6),
        _member(0.5, lower=0.3, upper=0.7),
        _member(0.5, lower=0.4, upper=0.95),
    ]
    result = aggregate(members, "weighted")
    assert result.lower == 0.3
    assert result.upper == 0.7


def test_empty_ensemble_raises() -> None:
    try:
        aggregate([], "median")
    except ValueError:
        return
    raise AssertionError("expected ValueError for empty ensemble")


@given(
    st.lists(
        st.tuples(
            st.floats(0.0, 1.0),
            st.floats(0.0, 1.0),
            st.floats(0.0, 1.0),
            st.floats(0.0, 1.0),
        ),
        min_size=1,
        max_size=6,
    ),
    st.sampled_from(["median", "weighted"]),
)
def test_aggregate_stays_in_the_unit_interval(
    raw: list[tuple[float, float, float, float]], method: str
) -> None:
    members = [_member(p, confidence=c, lower=lo, upper=hi) for p, c, lo, hi in raw]
    result = aggregate(members, method)  # type: ignore[arg-type]
    for value in (result.p_model, result.confidence, result.lower, result.upper):
        assert 0.0 <= value <= 1.0
