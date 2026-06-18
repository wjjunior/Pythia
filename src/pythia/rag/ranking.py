"""Pure ranking helpers for point-in-time retrieval (§4.5).

A candidate's final score is similarity x source reliability x freshness, where freshness decays
exponentially with the age of the evidence relative to the retrieval `asof`.
"""


def freshness(age_seconds: float, halflife_seconds: float = 86400.0) -> float:
    return float(0.5 ** (age_seconds / halflife_seconds))


def rank_score(similarity: float, reliability: float, freshness_weight: float) -> float:
    return similarity * reliability * freshness_weight
