"""Example tests for the §4.2 scoring functions: tables, boundaries, factor zeroing."""

import pytest

from pythia.scoring import (
    confidence,
    edge,
    kelly_fraction,
    net_edge,
    opportunity_score,
    position_size,
)


def test_edge_is_signed_difference() -> None:
    assert edge(0.6, 0.5) == pytest.approx(0.1)
    assert edge(0.4, 0.5) == pytest.approx(-0.1)
    assert edge(0.5, 0.5) == 0.0


def test_net_edge_subtracts_half_spread_and_costs() -> None:
    assert net_edge(0.10, 0.02, 0.01, 0.005) == pytest.approx(0.10 - 0.01 - 0.01 - 0.005)


def test_confidence_is_the_product_of_its_factors() -> None:
    assert confidence(0.8, 0.5, 0.5) == pytest.approx(0.2)


def test_confidence_is_zero_when_any_factor_is_zero() -> None:
    assert confidence(0.0, 1.0, 1.0) == 0.0
    assert confidence(1.0, 0.0, 1.0) == 0.0
    assert confidence(1.0, 1.0, 0.0) == 0.0


def test_opportunity_score_is_the_product_of_its_factors() -> None:
    assert opportunity_score(0.1, 0.8, 0.9, 0.9, 0.9, 0.9) == pytest.approx(0.1 * 0.8 * 0.9**4)


def test_opportunity_score_is_zero_when_any_penalty_is_zero() -> None:
    assert opportunity_score(0.1, 0.8, 0.0, 0.9, 0.9, 0.9) == 0.0
    assert opportunity_score(0.1, 0.8, 0.9, 0.9, 0.0, 0.9) == 0.0


def test_kelly_fraction_for_a_positive_net_edge() -> None:
    assert kelly_fraction(0.1, 0.5) == pytest.approx(0.2)


def test_kelly_fraction_is_zero_when_net_edge_not_positive() -> None:
    assert kelly_fraction(0.0, 0.5) == 0.0
    assert kelly_fraction(-0.1, 0.5) == 0.0


def test_kelly_fraction_is_zero_when_q_reaches_one() -> None:
    assert kelly_fraction(0.1, 1.0) == 0.0
    assert kelly_fraction(0.1, 1.2) == 0.0


def test_position_size_is_capped_by_cap_per_trade() -> None:
    assert position_size(10_000.0, 0.5, 0.25, 1.0, 50.0) == 50.0


def test_position_size_kelly_term_binds_below_the_cap() -> None:
    assert position_size(100.0, 0.2, 0.25, 1.0, 50.0) == pytest.approx(5.0)


def test_position_size_is_zero_when_confidence_is_zero() -> None:
    assert position_size(10_000.0, 0.5, 0.25, 0.0, 50.0) == 0.0
