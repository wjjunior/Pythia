"""Score assembly (§4.2/§4.3): combine the pure scoring primitives into a Score contract.

`ev_unit == edge_net` — a binary contract pays $1, so the net edge after costs is the expected
value per unit. The resolution penalty is graded by the resolution-source type (§4.4.2); the other
penalties are passed in by the caller.
"""

from enum import StrEnum

from pythia.common import Estimate, Score
from pythia.scoring.functions import edge, kelly_fraction, net_edge, opportunity_score


class ResolutionSourceType(StrEnum):
    CHAINLINK = "chainlink"
    CLEAN_BINARY = "clean_binary"
    SUBJECTIVE_UMA = "subjective_uma"


_RESOLUTION_PENALTY: dict[ResolutionSourceType, float] = {
    ResolutionSourceType.CHAINLINK: 0.95,
    ResolutionSourceType.CLEAN_BINARY: 0.85,
    ResolutionSourceType.SUBJECTIVE_UMA: 0.6,
}


def resolution_penalty(source_type: ResolutionSourceType) -> float:
    return _RESOLUTION_PENALTY[source_type]


def classify_resolution_source(resolution_source: str | None) -> ResolutionSourceType:
    if not resolution_source:
        return ResolutionSourceType.SUBJECTIVE_UMA
    text = resolution_source.lower()
    if "chainlink" in text:
        return ResolutionSourceType.CHAINLINK
    if "binary" in text:
        return ResolutionSourceType.CLEAN_BINARY
    return ResolutionSourceType.SUBJECTIVE_UMA


def build_score(
    estimate: Estimate,
    q: float,
    *,
    spread: float,
    fee: float,
    slippage: float,
    p_resol: float,
    p_liq: float,
    p_time: float,
    p_corr: float,
) -> Score:
    raw_edge = edge(estimate.p_model, q)
    edge_net = net_edge(raw_edge, spread, fee, slippage)
    ev_unit = edge_net
    return Score(
        estimate_id=estimate.id,
        edge=raw_edge,
        edge_net=edge_net,
        ev_unit=ev_unit,
        p_resol=p_resol,
        p_liq=p_liq,
        p_time=p_time,
        p_corr=p_corr,
        opportunity_score=opportunity_score(
            ev_unit, estimate.confidence, p_resol, p_liq, p_time, p_corr
        ),
        kelly_fraction=kelly_fraction(edge_net, q),
    )
