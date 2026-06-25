"""Pure scoring functions (System Design §4.2): edge, EV, ranking and Kelly sizing.

No clock, no randomness, no I/O. Inputs are assumed valid (validated at the boundaries).
"""


def edge(p_model: float, q: float) -> float:
    return p_model - q


def net_edge(raw_edge: float, spread: float, fee: float, slippage: float) -> float:
    return raw_edge - spread / 2 - fee - slippage


def confidence(calib_category: float, source_agreement: float, source_quality: float) -> float:
    return calib_category * source_agreement * source_quality


_EXTREME_PRICE_LO = 0.05
_EXTREME_PRICE_HI = 0.95

# Below this disagreement penalty the radar excludes the row outright (not just de-ranks it): the
# model is confidently overriding a near-certain market by more than (1 - floor) in probability.
MARKET_DISAGREEMENT_FLOOR = 0.5


def market_disagreement_penalty(p_model: float, q: float) -> float:
    # At an extreme price the market is near-certain; overriding it with no basis is the model's
    # "convincingly wrong" failure. Cut confidence by how far the estimate departs from the market.
    if _EXTREME_PRICE_LO < q < _EXTREME_PRICE_HI:
        return 1.0
    return 1.0 - abs(p_model - q)


def is_extreme_market_disagreement(p_model: float, q: float) -> bool:
    return market_disagreement_penalty(p_model, q) < MARKET_DISAGREEMENT_FLOOR


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
    return max(0.0, min(bankroll * kelly_frac * kelly_mult * conf, cap_per_trade))
