"""Example tests for the §4.2 scoring functions: tables, boundaries, factor zeroing."""

import pytest

from pythia.scoring import (
    confidence,
    edge,
    is_extreme_market_disagreement,
    kelly_fraction,
    market_disagreement_penalty,
    net_edge,
    opportunity_score,
    position_size,
)


def test_market_disagreement_penalty_is_neutral_in_the_mid_range() -> None:
    assert market_disagreement_penalty(0.9, 0.5) == 1.0
    assert market_disagreement_penalty(0.1, 0.4) == 1.0


def test_market_disagreement_penalty_distrusts_overriding_an_extreme_market() -> None:
    assert market_disagreement_penalty(0.85, 0.0005) == pytest.approx(0.1505)  # heavy distrust
    assert market_disagreement_penalty(0.03, 0.02) == pytest.approx(0.99)  # model agrees → mild
    assert market_disagreement_penalty(0.2, 0.98) == pytest.approx(0.22)  # high extreme, disagrees


def test_extreme_market_disagreement_flags_a_confident_override() -> None:
    # Model bets YES at 85% against a market priced at 0.05% — a confident override.
    assert is_extreme_market_disagreement(0.85, 0.0005) is True
    assert is_extreme_market_disagreement(0.99, 0.011) is True  # YES at 99% vs 1.1% market


def test_extreme_market_disagreement_keeps_a_model_that_agrees_with_the_extreme() -> None:
    # Model also rates the longshot low — agrees with the near-certain market, so it is kept.
    assert is_extreme_market_disagreement(0.01, 0.0005) is False


def test_extreme_market_disagreement_keeps_a_moderate_non_extreme_disagreement() -> None:
    assert is_extreme_market_disagreement(0.2, 0.55) is False  # mid-range market, never excluded
    assert is_extreme_market_disagreement(0.2, 0.04) is False  # extreme price, |diff| 0.16 <= 0.5


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
