"""Pure trader-metric tests (PYT-91, AC-S5): asymmetric risk, the scalper hard-cap, score floors."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pythia.smartmoney.metrics import (
    SelectionPolicy,
    market_class_ok,
    max_drawdown,
    risk_adj_edge,
    skewness,
    sortino,
    trader_score,
)

_POLICY = SelectionPolicy(
    min_resolved=20,
    skew_hard_cap=-1.0,
    w_edge=1.0,
    w_agree=1.0,
    w_cov=0.5,
    market_class_allow=frozenset({"politics", "sports"}),
    market_class_min_share=0.6,
)


def test_sortino_rewards_upside_volatility_unlike_sharpe() -> None:
    upside_vol = [0.5, 0.0, 0.5, 0.0, -0.05]  # big upside swings, tiny downside
    downside_vol = [0.05, 0.05, 0.05, 0.05, -0.5]  # tiny wins, one big loss (the scalper tell)
    assert sortino(upside_vol) > sortino(downside_vol)  # Sortino prefers the upside-heavy profile


def test_sortino_is_infinite_without_downside_and_raises_on_empty() -> None:
    assert sortino([0.1, 0.2, 0.3]) == float("inf")
    with pytest.raises(ValueError):
        sortino([])


def test_max_drawdown_is_zero_for_a_monotone_rise_and_the_drop_size_otherwise() -> None:
    assert max_drawdown([0.1, 0.2, 0.1]) == pytest.approx(0.0)  # cumsum 0.1, 0.3, 0.4 — monotone
    assert max_drawdown([0.5, -0.8, 0.1]) == pytest.approx(0.8)  # equity 0.5, -0.3 → drop 0.8


@given(st.lists(st.floats(min_value=-5.0, max_value=5.0), min_size=1, max_size=50))
def test_max_drawdown_is_never_negative(returns: list[float]) -> None:
    assert max_drawdown(returns) >= 0.0


def test_scalper_profile_is_hard_capped_by_negative_skew() -> None:
    scalper = [0.05] * 25 + [-1.2]  # many tiny wins, a rare catastrophic loss
    skew = skewness(scalper)
    assert skew < _POLICY.skew_hard_cap  # strongly negative

    score = trader_score(
        risk_adj_edge=risk_adj_edge(scalper),
        agreed_bpd=0.2,
        coverage=1.0,
        n_resolved=len(scalper),
        market_class_ok=True,
        skew=skew,
        policy=_POLICY,
    )
    assert score == float("-inf")  # never promotable despite a 96% win rate (AC-S5)


def test_a_balanced_profile_scores_finite() -> None:
    healthy = [0.1, -0.05, 0.2, -0.1, 0.15, -0.08] * 5  # n = 30, symmetric-ish
    skew = skewness(healthy)
    assert skew >= _POLICY.skew_hard_cap

    score = trader_score(
        risk_adj_edge=risk_adj_edge(healthy),
        agreed_bpd=0.15,
        coverage=1.0,
        n_resolved=len(healthy),
        market_class_ok=True,
        skew=skew,
        policy=_POLICY,
    )
    assert math.isfinite(score)


def test_trader_score_hard_floors() -> None:
    def score(*, n_resolved: int, market_class_ok: bool, skew: float) -> float:
        return trader_score(
            risk_adj_edge=1.0,
            agreed_bpd=0.2,
            coverage=1.0,
            n_resolved=n_resolved,
            market_class_ok=market_class_ok,
            skew=skew,
            policy=_POLICY,
        )

    assert score(n_resolved=5, market_class_ok=True, skew=0.0) == float("-inf")  # n < min
    assert score(n_resolved=30, market_class_ok=False, skew=0.0) == float("-inf")  # market-class
    assert score(n_resolved=30, market_class_ok=True, skew=-2.0) == float("-inf")  # skew cap
    assert math.isfinite(score(n_resolved=30, market_class_ok=True, skew=0.0))


def test_market_class_ok_requires_a_research_amenable_majority() -> None:
    assert market_class_ok(["politics", "sports", "politics"], _POLICY) is True
    assert market_class_ok(["politics", "crypto", "crypto"], _POLICY) is False  # 1/3 < 0.6
    assert market_class_ok([], _POLICY) is False
