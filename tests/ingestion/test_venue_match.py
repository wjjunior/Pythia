"""Tests for the pure cross-venue event matcher (§4.10): confidence + the discard gate."""

from datetime import UTC, datetime

from pythia.ingestion.venue_match import (
    MATCH_CONFIDENCE_MIN,
    MatchCandidate,
    best_match,
    is_match,
    match_confidence,
)

CLOSE = datetime(2026, 6, 22, 18, 0, tzinfo=UTC)


def _c(question: str, close: datetime | None = CLOSE) -> MatchCandidate:
    return MatchCandidate(question=question, close_time=close)


def test_identical_event_is_high_confidence() -> None:
    confidence = match_confidence(
        _c("Will Switzerland win on 2026-06-22?"), _c("Will Switzerland win on 2026-06-22?")
    )
    assert confidence > 0.95
    assert is_match(confidence)


def test_unrelated_event_is_discarded() -> None:
    confidence = match_confidence(
        _c("Will Switzerland win on 2026-06-22?"),
        _c("Will the Fed cut rates in July?", close=datetime(2026, 7, 30, tzinfo=UTC)),
    )
    assert confidence < MATCH_CONFIDENCE_MIN
    assert not is_match(confidence)


def test_missing_close_times_cap_confidence_below_one() -> None:
    confidence = match_confidence(
        _c("Same question text", close=None), _c("Same question text", close=None)
    )
    assert confidence < 1.0  # timing component is zero without both close times


def test_best_match_picks_highest_and_discards_below_threshold() -> None:
    ours = _c("Will Switzerland beat Germany on 2026-06-22?")
    candidates = [
        (
            "weak",
            _c(
                "Completely different market about oil prices",
                close=datetime(2027, 1, 1, tzinfo=UTC),
            ),
        ),
        ("strong", _c("Will Switzerland beat Germany on 2026-06-22?")),
    ]
    match = best_match(ours, candidates)
    assert match is not None
    key, confidence = match
    assert key == "strong"
    assert confidence > MATCH_CONFIDENCE_MIN


def test_best_match_returns_none_when_all_below_threshold() -> None:
    ours = _c("Will Switzerland beat Germany on 2026-06-22?")
    candidates = [("a", _c("Unrelated oil market", close=datetime(2027, 1, 1, tzinfo=UTC)))]
    assert best_match(ours, candidates) is None
