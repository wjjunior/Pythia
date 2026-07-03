"""Tests for score assembly: ev_unit = edge_net, penalties carried, resolution grading."""

import pytest

from pythia.common import Estimate, Score, SignalSource
from pythia.scoring import (
    ResolutionSourceType,
    build_score,
    classify_resolution_source,
    resolution_penalty,
)


def _estimate(*, p_model: float = 0.7, confidence: float = 0.6) -> Estimate:
    return Estimate(
        id="e1",
        market_id="m1",
        snapshot_id="s1",
        p_model=p_model,
        confidence=confidence,
        lower=0.6,
        upper=0.8,
        rationale="r",
        evidence_ids=["ev1"],
        model_name="stub",
        signal_source=SignalSource.UNKNOWN,
    )


def test_classify_defaults_to_subjective_uma() -> None:
    assert classify_resolution_source(None) == ResolutionSourceType.SUBJECTIVE_UMA
    assert (
        classify_resolution_source("UMA optimistic oracle") == ResolutionSourceType.SUBJECTIVE_UMA
    )


def test_classify_detects_chainlink() -> None:
    assert classify_resolution_source("Chainlink price feed") == ResolutionSourceType.CHAINLINK


def test_classify_detects_clean_binary() -> None:
    assert (
        classify_resolution_source("Official binary settlement")
        == ResolutionSourceType.CLEAN_BINARY
    )


def test_resolution_penalty_grades_safer_sources_higher() -> None:
    assert resolution_penalty(ResolutionSourceType.CHAINLINK) > resolution_penalty(
        ResolutionSourceType.SUBJECTIVE_UMA
    )
    for source_type in ResolutionSourceType:
        assert 0.0 < resolution_penalty(source_type) <= 1.0


def test_build_score_uses_edge_net_as_ev_unit() -> None:
    score = build_score(
        _estimate(p_model=0.7, confidence=0.6),
        q=0.5,
        spread=0.02,
        fee=0.0,
        slippage=0.0,
        p_resol=0.6,
        p_liq=1.0,
        p_time=1.0,
        p_corr=1.0,
    )
    assert isinstance(score, Score)
    assert score.estimate_id == "e1"
    assert score.edge == pytest.approx(0.2)
    assert score.edge_net == pytest.approx(0.19)
    assert score.ev_unit == score.edge_net
    assert score.opportunity_score == pytest.approx(score.ev_unit * 0.6 * 0.6)
    assert score.kelly_fraction == pytest.approx(0.19 / 0.5)


def test_build_score_carries_the_penalties() -> None:
    score = build_score(
        _estimate(),
        q=0.5,
        spread=0.0,
        fee=0.0,
        slippage=0.0,
        p_resol=0.6,
        p_liq=0.9,
        p_time=0.8,
        p_corr=0.7,
    )
    assert (score.p_resol, score.p_liq, score.p_time, score.p_corr) == (0.6, 0.9, 0.8, 0.7)
