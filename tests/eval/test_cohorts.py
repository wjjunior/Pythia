"""Unit tests for eval cohorts (PYT-95): config-defined, point-in-time, composable."""

from pythia.eval.cohorts import Cohort, CohortPolicy, cohorts_for

_POLICY = CohortPolicy(max_horizon_days=7.0, price_min=0.10, price_max=0.85)


def test_fast_7d_matches_within_horizon_and_excludes_beyond() -> None:
    assert Cohort.FAST_7D in cohorts_for(price=0.5, horizon_days=3.0, policy=_POLICY)
    assert Cohort.FAST_7D not in cohorts_for(price=0.5, horizon_days=30.0, policy=_POLICY)


def test_fast_7d_absent_when_horizon_unknown() -> None:
    assert Cohort.FAST_7D not in cohorts_for(price=0.5, horizon_days=None, policy=_POLICY)


def test_midband_excludes_heavy_favorites_and_longshots() -> None:
    assert Cohort.MIDBAND in cohorts_for(price=0.5, horizon_days=None, policy=_POLICY)
    assert Cohort.MIDBAND not in cohorts_for(price=0.95, horizon_days=None, policy=_POLICY)
    assert Cohort.MIDBAND not in cohorts_for(price=0.03, horizon_days=None, policy=_POLICY)


def test_cohorts_compose() -> None:
    both = cohorts_for(price=0.5, horizon_days=3.0, policy=_POLICY)
    assert both == frozenset({Cohort.FAST_7D, Cohort.MIDBAND})


def test_boundaries_are_inclusive() -> None:
    assert Cohort.MIDBAND in cohorts_for(price=0.10, horizon_days=None, policy=_POLICY)
    assert Cohort.MIDBAND in cohorts_for(price=0.85, horizon_days=None, policy=_POLICY)
    assert Cohort.FAST_7D in cohorts_for(price=0.5, horizon_days=7.0, policy=_POLICY)
