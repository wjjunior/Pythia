"""Resolution & dispute risk (§4.4.2): grade P_resol by the measured per-category invalid-rate.

Pure: no I/O, no clock. UMA settlements can resolve invalid (50/50); a recent per-category invalid
base rate scales the source-type penalty (an invalid market realizes no edge) and, past a threshold,
gates the category to paper.
"""


def invalid_rate(invalid_count: int, total: int) -> float:
    return invalid_count / total if total > 0 else 0.0


def grade_p_resol(base_penalty: float, invalid_rate: float) -> float:
    graded = base_penalty * (1.0 - invalid_rate)
    return min(max(graded, 0.0), 1.0)


def is_resolution_gated(invalid_rate: float, max_invalid_rate: float) -> bool:
    return invalid_rate > max_invalid_rate
