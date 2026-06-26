"""Unit tests for the pure trader-scoring helpers (PYT-96, §4.2)."""

import pytest

from pythia.smartmoney.scoring import AgreementSample, agreement_metrics, position_return


def test_position_return_pays_a_winner_and_forfeits_a_loser() -> None:
    assert position_return(won=True, entry_price=0.4) == pytest.approx((1.0 - 0.4) / 0.4)
    assert position_return(won=False, entry_price=0.4) == -1.0


def _sample(
    agreed: bool, *, p: float, market: float, outcome: int, weight: float
) -> AgreementSample:
    return AgreementSample(
        agreed=agreed, p_model=p, market_price=market, outcome=outcome, weight=weight
    )


def test_agreement_metrics_empty_and_none_agreed() -> None:
    assert agreement_metrics([]) == (0.0, 0.0)
    assert agreement_metrics([_sample(False, p=0.8, market=0.5, outcome=1, weight=1.0)]) == (
        0.0,
        0.0,
    )


def test_agree_rate_is_coverage_and_bpd_is_dollar_weighted_brier_improvement() -> None:
    samples = [
        _sample(True, p=0.8, market=0.5, outcome=1, weight=2.0),  # we beat the market
        _sample(False, p=0.3, market=0.5, outcome=1, weight=5.0),  # not agreed → excluded from bpd
    ]
    agree_rate, agreed_bpd = agreement_metrics(samples)
    assert agree_rate == pytest.approx(0.5)  # 1 of 2 agreed
    # brier(0.5,1)=0.25, brier(0.8,1)=0.04 → improvement 0.21, single agreed sample
    assert agreed_bpd == pytest.approx(0.21)


def test_bpd_is_negative_when_our_forecast_is_worse_than_the_market() -> None:
    samples = [_sample(True, p=0.2, market=0.5, outcome=1, weight=1.0)]  # market 0.5 beat our 0.2
    _, agreed_bpd = agreement_metrics(samples)
    # brier(0.5,1)=0.25, brier(0.2,1)=0.64 → improvement -0.39
    assert agreed_bpd == pytest.approx(-0.39)


def test_bpd_weights_by_dollar_size() -> None:
    samples = [
        _sample(True, p=0.9, market=0.5, outcome=1, weight=9.0),  # big, strong improvement
        _sample(True, p=0.5, market=0.5, outcome=1, weight=1.0),  # small, zero improvement
    ]
    _, agreed_bpd = agreement_metrics(samples)
    # (9*(0.25-0.01) + 1*0.0) / 10 = 2.16/10 — within the 5x-median cap, so weighting is unchanged
    assert agreed_bpd == pytest.approx(0.216)


def test_agreed_bpd_winsorises_an_oversized_lucky_fill_to_the_weight_cap() -> None:
    # PYT-127: 20 small agreed fills worse than the market + one huge lucky win. Raw weighting
    # would let the whale's improvement at weight 1000 dominate and read positive (promotable); the
    # winsorised weight (5x the median = 5) gives it no more pull than a fill at the cap.
    small = [_sample(True, p=0.6, market=0.8, outcome=1, weight=1.0) for _ in range(20)]
    whale_at_cap = _sample(True, p=0.95, market=0.5, outcome=1, weight=5.0)
    whale_oversized = _sample(True, p=0.95, market=0.5, outcome=1, weight=1000.0)

    _, capped = agreement_metrics([*small, whale_at_cap])
    _, winsorised = agreement_metrics([*small, whale_oversized])

    assert winsorised == pytest.approx(capped)  # the 1000x fill is winsorised down to the cap
    assert winsorised < 0.0  # the 20 worse-than-market fills win — one lucky whale can't promote it
