"""Calibration-priority helpers: bet side, entry price, win multiple, days-to-close.

Pure (no clock, no I/O): time enters as the `asof` parameter. Used to rank the radar toward
near-term, high-payout options so resolutions — and calibration data — arrive quickly.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pythia.common import Estimate, Market, Score, Side
from pythia.scoring.functions import is_extreme_market_disagreement

_SECONDS_PER_DAY = 86400.0
# A degenerate 0/1 price would give an infinite payout; clamp so the value stays JSON-finite.
_MIN_ENTRY_PRICE = 1e-6


def recommended_side(p_model: float, q: float) -> Side:
    return Side.YES if p_model >= q else Side.NO


def entry_price(side: Side, q: float) -> float:
    return q if side is Side.YES else 1.0 - q


def win_multiple(entry: float) -> float:
    return 1.0 / max(entry, _MIN_ENTRY_PRICE)


def win_probability(side: Side, p_model: float) -> float:
    return p_model if side is Side.YES else 1.0 - p_model


def closes_in_days(close_time: datetime | None, asof: datetime) -> float | None:
    if close_time is None:
        return None
    return (close_time - asof).total_seconds() / _SECONDS_PER_DAY


@dataclass(frozen=True, slots=True)
class RadarMetrics:
    recommended_side: Side
    entry_price: float
    win_multiple: float
    win_probability: float
    closes_in_days: float | None
    resolves_in_days: float | None


def radar_metrics(
    *,
    p_model: float,
    q: float,
    close_time: datetime | None,
    resolves_at: datetime | None,
    asof: datetime,
) -> RadarMetrics:
    side = recommended_side(p_model, q)
    entry = entry_price(side, q)
    effective_resolution = resolves_at if resolves_at is not None else close_time
    return RadarMetrics(
        recommended_side=side,
        entry_price=entry,
        win_multiple=win_multiple(entry),
        win_probability=win_probability(side, p_model),
        closes_in_days=closes_in_days(close_time, asof),
        resolves_in_days=closes_in_days(effective_resolution, asof),
    )


def _resolves_in_days_key(metrics: RadarMetrics) -> float:
    return metrics.resolves_in_days if metrics.resolves_in_days is not None else float("inf")


def rank_radar(
    rows: Sequence[tuple[Score, Estimate, Market, float]],
    *,
    asof: datetime,
    limit: int,
    windowed: bool,
    min_win_multiple: float | None = None,
    min_win_probability: float | None = None,
) -> list[tuple[Score, Estimate, Market, RadarMetrics]]:
    """Rank fetched radar candidates: drop confident overrides of a near-certain market (PYT-84),
    attach per-row metrics, apply the win filters, order toward soonest resolution when windowed.
    """
    kept = [
        (
            score,
            estimate,
            market,
            radar_metrics(
                p_model=estimate.p_model,
                q=q,
                close_time=market.close_time,
                resolves_at=market.resolves_at,
                asof=asof,
            ),
        )
        for score, estimate, market, q in rows
        if not is_extreme_market_disagreement(estimate.p_model, q)
    ]
    if min_win_multiple is not None:
        kept = [row for row in kept if row[3].win_multiple >= min_win_multiple]
    if min_win_probability is not None:
        kept = [row for row in kept if row[3].win_probability >= min_win_probability]
    if windowed:
        kept.sort(key=lambda row: (_resolves_in_days_key(row[3]), -row[0].opportunity_score))
    return kept[:limit]
