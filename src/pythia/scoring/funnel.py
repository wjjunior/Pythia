"""Per-tick pipeline stage funnel (PYT-164): pure counting that makes coverage-vs-no-edge visible.

A diagnostic, not a gate. It classifies each scored market against the operator's *default* radar
filters as read-only predicates, so a near-empty Radar can be read for what it is: a coverage gap
(few/repeated markets crawled) versus the honest result of an efficient market (markets scored but
correctly rejected by the stacked radar gates). It alters no threshold, score, or ranking.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum

from pythia.common import PipelineFunnel
from pythia.scoring.functions import is_extreme_market_disagreement
from pythia.scoring.prioritization import radar_metrics

# Mirror the operator's default radar filters (dashboard DEFAULT_RADAR_FILTERS / CALIBRATION_*), so
# the funnel reports survival against what the operator sees. Diagnostic — never a live gate.
RADAR_FUNNEL_MAX_DAYS = 7
RADAR_FUNNEL_MIN_WIN_MULTIPLE = 1.2
RADAR_FUNNEL_MIN_WIN_PROBABILITY = 0.5


class RadarGate(StrEnum):
    """Where a scored market lands against the radar gates — exactly one bucket per market."""

    SURFACED = "surfaced"
    DROPPED_WINDOW = "dropped_window"
    DROPPED_WIN_MULTIPLE = "dropped_win_multiple"
    DROPPED_WIN_PROBABILITY = "dropped_win_probability"
    DROPPED_EXTREME_DISAGREEMENT = "dropped_extreme_disagreement"


def _passes_window(
    close_time: datetime | None, resolves_at: datetime | None, asof: datetime, max_days: int
) -> bool:
    # Mirrors the radar SQL gate: still enterable (close_time absent or future) AND resolving within
    # the near-term window. Effective resolution is UMA settlement, falling back to trading-close.
    if close_time is not None and close_time < asof:
        return False
    effective = resolves_at if resolves_at is not None else close_time
    if effective is None:
        return False
    return asof <= effective <= asof + timedelta(days=max_days)


def classify_radar_gate(
    *,
    p_model: float,
    q: float,
    close_time: datetime | None,
    resolves_at: datetime | None,
    resolved: bool,
    asof: datetime,
    max_days: int = RADAR_FUNNEL_MAX_DAYS,
    min_win_multiple: float = RADAR_FUNNEL_MIN_WIN_MULTIPLE,
    min_win_probability: float = RADAR_FUNNEL_MIN_WIN_PROBABILITY,
) -> RadarGate:
    """Attribute a scored market to the FIRST radar gate it fails (or SURFACED).

    Order mirrors the live path — the SQL window/entry gate, then ``rank_radar``'s disagreement
    exclusion, then the win-multiple and win-probability filters — so each market lands in exactly
    one bucket and the drop counts sum to ``scored - radar_surfaced``.
    """
    if resolved or not _passes_window(close_time, resolves_at, asof, max_days):
        return RadarGate.DROPPED_WINDOW
    if is_extreme_market_disagreement(p_model, q):
        return RadarGate.DROPPED_EXTREME_DISAGREEMENT
    metrics = radar_metrics(
        p_model=p_model, q=q, close_time=close_time, resolves_at=resolves_at, asof=asof
    )
    if metrics.win_multiple < min_win_multiple:
        return RadarGate.DROPPED_WIN_MULTIPLE
    if metrics.win_probability < min_win_probability:
        return RadarGate.DROPPED_WIN_PROBABILITY
    return RadarGate.SURFACED


def funnel_from_gates(
    *,
    crawled: int,
    tracked_open: int,
    shortlisted: int,
    estimated: int,
    scored: int,
    gates: Mapping[RadarGate, int],
    categories: Mapping[str, int],
) -> PipelineFunnel:
    """Assemble a funnel from the stage counts and the per-gate tally of the scored markets."""
    return PipelineFunnel(
        crawled=crawled,
        tracked_open=tracked_open,
        shortlisted=shortlisted,
        estimated=estimated,
        scored=scored,
        radar_surfaced=gates.get(RadarGate.SURFACED, 0),
        dropped_window=gates.get(RadarGate.DROPPED_WINDOW, 0),
        dropped_win_multiple=gates.get(RadarGate.DROPPED_WIN_MULTIPLE, 0),
        dropped_win_probability=gates.get(RadarGate.DROPPED_WIN_PROBABILITY, 0),
        dropped_extreme_disagreement=gates.get(RadarGate.DROPPED_EXTREME_DISAGREEMENT, 0),
        categories=dict(categories),
    )
