"""Unit + property tests for the event coupling prior (PYT-139)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pythia.common import EventGroup, EventOption, EventType
from pythia.scoring.events import (
    _isotonic_decreasing,
    _project_simplex,
    couple_probabilities,
)


def _group(event_type: EventType, n: int, thresholds: list[float] | None = None) -> EventGroup:
    return EventGroup(
        event_slug="e",
        event_type=event_type,
        options=[
            EventOption(
                market_id=str(i),
                question="q",
                yes_price=0.5,
                spread=0.02,
                threshold=(thresholds[i] if thresholds else None),
            )
            for i in range(n)
        ],
    )


def _p(*values: float) -> dict[str, float]:
    return {str(i): v for i, v in enumerate(values)}


def test_mutually_exclusive_probabilities_sum_to_one() -> None:
    out = couple_probabilities(_group(EventType.MUTUALLY_EXCLUSIVE, 3), _p(0.8, 0.4, 0.6))
    assert sum(out.values()) == pytest.approx(1.0)


def test_all_zero_exclusive_becomes_uniform() -> None:
    out = couple_probabilities(_group(EventType.MUTUALLY_EXCLUSIVE, 2), _p(0.0, 0.0))
    assert out == pytest.approx({"0": 0.5, "1": 0.5})


def test_threshold_ladder_is_made_non_increasing() -> None:
    group = _group(EventType.THRESHOLD_LADDER, 3, thresholds=[1.0, 2.0, 3.0])
    out = couple_probabilities(group, _p(0.5, 0.7, 0.6))  # input violates non-increasing
    vals = [out[str(i)] for i in range(3)]
    assert all(vals[i] >= vals[i + 1] - 1e-12 for i in range(2))


def test_unknown_event_is_unchanged() -> None:
    assert couple_probabilities(_group(EventType.UNKNOWN, 2), _p(0.3, 0.9)) == _p(0.3, 0.9)


def test_single_option_is_unchanged() -> None:
    assert couple_probabilities(_group(EventType.MUTUALLY_EXCLUSIVE, 1), _p(0.7)) == _p(0.7)


def test_coupling_is_idempotent() -> None:
    group = _group(EventType.MUTUALLY_EXCLUSIVE, 3)
    once = couple_probabilities(group, _p(0.8, 0.4, 0.6))
    assert couple_probabilities(group, once) == pytest.approx(once)


@given(
    p=st.lists(st.floats(0.0, 1.0), min_size=2, max_size=6),
    q_raw=st.lists(st.floats(0.01, 1.0), min_size=2, max_size=6),
)
def test_simplex_projection_never_increases_error_vs_a_valid_distribution(
    p: list[float], q_raw: list[float]
) -> None:
    n = min(len(p), len(q_raw))
    p, q_raw = p[:n], q_raw[:n]
    total = sum(q_raw)
    q = [x / total for x in q_raw]  # a valid distribution (sum 1)
    proj = _project_simplex(p)
    err_proj = sum((a - b) ** 2 for a, b in zip(proj, q, strict=True))
    err_p = sum((a - b) ** 2 for a, b in zip(p, q, strict=True))
    assert err_proj <= err_p + 1e-9


@given(
    p=st.lists(st.floats(0.0, 1.0), min_size=2, max_size=6),
    q=st.lists(st.floats(0.0, 1.0), min_size=2, max_size=6),
)
def test_isotonic_projection_never_increases_error_vs_a_monotone_target(
    p: list[float], q: list[float]
) -> None:
    n = min(len(p), len(q))
    p = p[:n]
    target = sorted(q[:n], reverse=True)  # a valid non-increasing target
    proj = _isotonic_decreasing(p)
    err_proj = sum((a - b) ** 2 for a, b in zip(proj, target, strict=True))
    err_p = sum((a - b) ** 2 for a, b in zip(p, target, strict=True))
    assert err_proj <= err_p + 1e-9
