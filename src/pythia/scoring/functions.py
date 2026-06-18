"""Pure scoring functions (System Design §4.2): edge, EV, ranking and Kelly sizing.

No clock, no randomness, no I/O. Inputs are assumed valid (validated at the boundaries).
"""


def edge(p_model: float, q: float) -> float:
    return p_model - q


def net_edge(raw_edge: float, spread: float, fee: float, slippage: float) -> float:
    return raw_edge - spread / 2 - fee - slippage


def confidence(calib_category: float, source_agreement: float, source_quality: float) -> float:
    return calib_category * source_agreement * source_quality


def opportunity_score(
    ev_unit: float,
    conf: float,
    p_resol: float,
    p_liq: float,
    p_time: float,
    p_corr: float,
) -> float:
    return ev_unit * conf * p_resol * p_liq * p_time * p_corr


def kelly_fraction(edge_net: float, q: float) -> float:
    return max(0.0, edge_net) / (1.0 - q) if q < 1.0 else 0.0


def position_size(
    bankroll: float,
    kelly_frac: float,
    kelly_mult: float,
    conf: float,
    cap_per_trade: float,
) -> float:
    return min(bankroll * kelly_frac * kelly_mult * conf, cap_per_trade)
