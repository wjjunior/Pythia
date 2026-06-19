"""Cross-venue event matcher (§4.10): are two markets the same event? Pure, no I/O.

Confidence blends question-text similarity with resolution-timing proximity (the same event resolves
at the same time). Matches below MATCH_CONFIDENCE_MIN are discarded, never trusted — a wrong match
would poison the divergence signal. This is a reference signal, not arbitrage: there is deliberately
no execution path here.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher

MATCH_CONFIDENCE_MIN = 0.6
_CLOSE_WINDOW = timedelta(days=3)
_QUESTION_WEIGHT = 0.7
_CLOSE_WEIGHT = 0.3


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    question: str
    close_time: datetime | None


def _text_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _close_proximity(a: datetime | None, b: datetime | None) -> float:
    if a is None or b is None:
        return 0.0
    gap = abs((a - b).total_seconds())
    return max(0.0, 1.0 - gap / _CLOSE_WINDOW.total_seconds())


def match_confidence(ours: MatchCandidate, theirs: MatchCandidate) -> float:
    question = _text_similarity(ours.question, theirs.question)
    timing = _close_proximity(ours.close_time, theirs.close_time)
    return _QUESTION_WEIGHT * question + _CLOSE_WEIGHT * timing


def is_match(confidence: float) -> bool:
    return confidence >= MATCH_CONFIDENCE_MIN


def best_match[K](
    ours: MatchCandidate, candidates: Iterable[tuple[K, MatchCandidate]]
) -> tuple[K, float] | None:
    best: tuple[K, float] | None = None
    for key, candidate in candidates:
        confidence = match_confidence(ours, candidate)
        if is_match(confidence) and (best is None or confidence > best[1]):
            best = (key, confidence)
    return best
