"""Tests for the post-mortem trigger + attribution (§5.5): surprise, drift, calibrated loss."""

from datetime import UTC, datetime, timedelta

from pythia.common import Evidence, FailureCase, FailureClass
from pythia.eval.postmortem import (
    StubAttributionAgent,
    is_category_drifting,
    is_surprising,
    surprise_score,
)

ASOF = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
_THRESH = 0.6
_MIN_CONF = 0.6


def _evidence(observed_at: datetime, eid: str = "e1") -> Evidence:
    return Evidence(
        id=eid,
        market_id="m",
        source_id="s",
        observed_at=observed_at,
        content="evidence",
    )


def test_surprise_is_high_only_on_a_confident_miss() -> None:
    assert surprise_score(0.95, 0.9, 0) > 0.7  # confident YES, resolved NO
    assert surprise_score(0.95, 0.9, 1) < 0.05  # confident YES, resolved YES


def test_confident_miss_is_flagged() -> None:
    assert is_surprising(0.95, 0.9, 0, threshold=_THRESH, min_confidence=_MIN_CONF)


def test_ordinary_calibrated_loss_is_not_flagged() -> None:
    # A 70% call that lost: brier 0.49 * conf 0.7 = 0.343 < 0.6 — must NOT trigger a post-mortem.
    assert not is_surprising(0.7, 0.7, 0, threshold=_THRESH, min_confidence=_MIN_CONF)


def test_low_confidence_miss_is_not_flagged() -> None:
    assert not is_surprising(0.99, 0.5, 0, threshold=_THRESH, min_confidence=_MIN_CONF)


def test_drift_detected_when_recent_window_loses_ground_and_trails_market() -> None:
    deltas = [0.1] * 20 + [-0.05] * 20  # was beating the market, now behind it
    assert is_category_drifting(deltas, window=20)


def test_no_drift_when_stable_and_still_ahead() -> None:
    assert not is_category_drifting([0.1] * 40, window=20)


def test_no_drift_without_enough_history() -> None:
    assert not is_category_drifting([-0.2] * 10, window=20)


def _case(*, confidence: float, ambiguous: bool, evidence: list[Evidence]) -> FailureCase:
    return FailureCase(
        market_id="m",
        estimate_id="est",
        category="politics",
        p_model=0.9,
        confidence=confidence,
        outcome=0,
        surprise=0.8,
        asof=ASOF,
        resolution_ambiguous=ambiguous,
        evidence=evidence,
    )


def test_attribution_flags_resolution_ambiguity_first() -> None:
    agent = StubAttributionAgent()
    verdict = agent.attribute(_case(confidence=0.9, ambiguous=True, evidence=[_evidence(ASOF)]))
    assert verdict.failure_class == FailureClass.RESOLUTION_AMBIGUITY


def test_attribution_flags_missing_evidence() -> None:
    agent = StubAttributionAgent()
    verdict = agent.attribute(_case(confidence=0.9, ambiguous=False, evidence=[]))
    assert verdict.failure_class == FailureClass.MISSING_EVIDENCE


def test_attribution_flags_stale_evidence() -> None:
    agent = StubAttributionAgent(stale_evidence_days=30)
    old = _evidence(ASOF - timedelta(days=90))
    verdict = agent.attribute(_case(confidence=0.9, ambiguous=False, evidence=[old]))
    assert verdict.failure_class == FailureClass.STALE_EVIDENCE


def test_attribution_flags_overconfidence_with_fresh_evidence() -> None:
    agent = StubAttributionAgent()
    fresh = _evidence(ASOF - timedelta(days=1))
    verdict = agent.attribute(_case(confidence=0.9, ambiguous=False, evidence=[fresh]))
    assert verdict.failure_class == FailureClass.MODEL_OVERCONFIDENCE


def test_attribution_can_clear_as_calibrated_loss() -> None:
    agent = StubAttributionAgent()
    fresh = _evidence(ASOF - timedelta(days=1))
    verdict = agent.attribute(_case(confidence=0.62, ambiguous=False, evidence=[fresh]))
    assert verdict.failure_class == FailureClass.CALIBRATED_LOSS
