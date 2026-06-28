"""Pure tests for the pipeline stage funnel (PYT-164): per-gate attribution + reconciliation."""

from collections import Counter
from datetime import UTC, datetime, timedelta

from pythia.common import PipelineFunnel
from pythia.scoring.funnel import RadarGate, classify_radar_gate, funnel_from_gates

_ASOF = datetime(2026, 6, 28, tzinfo=UTC)
_SOON = _ASOF + timedelta(days=2)


def _classify(
    *,
    p_model: float = 0.7,
    q: float = 0.6,
    close_time: datetime | None = _SOON,
    resolves_at: datetime | None = _SOON,
    resolved: bool = False,
) -> RadarGate:
    return classify_radar_gate(
        p_model=p_model,
        q=q,
        close_time=close_time,
        resolves_at=resolves_at,
        resolved=resolved,
        asof=_ASOF,
    )


def test_surfaced_when_within_window_payout_and_probability_clear() -> None:
    assert _classify() is RadarGate.SURFACED


def test_dropped_window_when_market_already_resolved() -> None:
    assert _classify(resolved=True) is RadarGate.DROPPED_WINDOW


def test_dropped_window_when_no_resolution_date() -> None:
    assert _classify(close_time=None, resolves_at=None) is RadarGate.DROPPED_WINDOW


def test_dropped_window_when_resolution_is_beyond_the_near_term_window() -> None:
    far = _ASOF + timedelta(days=10)
    assert _classify(close_time=far, resolves_at=far) is RadarGate.DROPPED_WINDOW


def test_dropped_window_when_trading_close_has_passed() -> None:
    assert _classify(close_time=_ASOF - timedelta(days=1)) is RadarGate.DROPPED_WINDOW


def test_dropped_extreme_disagreement_overrides_a_near_certain_market() -> None:
    # q=0.97 (market near-certain), p_model=0.4 (we disagree by more than the floor) → excluded.
    assert _classify(q=0.97, p_model=0.4) is RadarGate.DROPPED_EXTREME_DISAGREEMENT


def test_dropped_win_multiple_when_the_payout_is_too_small() -> None:
    # q=0.85 on the YES side → entry 0.85 → win_multiple 1.18 < 1.2.
    assert _classify(q=0.85, p_model=0.9) is RadarGate.DROPPED_WIN_MULTIPLE


def test_dropped_win_probability_when_confidence_is_below_half() -> None:
    # q=0.3 YES → win_multiple 3.3 clears, but win_probability 0.45 < 0.5.
    assert _classify(q=0.3, p_model=0.45) is RadarGate.DROPPED_WIN_PROBABILITY


def test_window_is_attributed_before_the_win_filters() -> None:
    # Fails the window AND would fail win_multiple — the first gate (window) claims it.
    assert _classify(resolved=True, q=0.9, p_model=0.95) is RadarGate.DROPPED_WINDOW


def test_funnel_reconciles_when_ordered_and_drops_sum() -> None:
    funnel = funnel_from_gates(
        crawled=10,
        tracked_open=10,
        shortlisted=8,
        estimated=6,
        scored=5,
        gates=Counter(
            {
                RadarGate.SURFACED: 2,
                RadarGate.DROPPED_WINDOW: 1,
                RadarGate.DROPPED_WIN_MULTIPLE: 1,
                RadarGate.DROPPED_WIN_PROBABILITY: 1,
            }
        ),
        categories={"politics": 6, "sports": 4},
    )

    assert funnel.reconciles
    assert funnel.radar_surfaced == 2 and funnel.dropped_window == 1
    assert funnel.scored - funnel.radar_surfaced == 3


def test_funnel_does_not_reconcile_when_ordering_is_violated() -> None:
    bad = PipelineFunnel(
        crawled=1,
        tracked_open=2,  # > crawled — impossible
        shortlisted=0,
        estimated=0,
        scored=0,
        radar_surfaced=0,
        dropped_window=0,
        dropped_win_multiple=0,
        dropped_win_probability=0,
        dropped_extreme_disagreement=0,
        categories={},
    )
    assert not bad.reconciles


def test_funnel_does_not_reconcile_when_drops_do_not_sum() -> None:
    bad = PipelineFunnel(
        crawled=5,
        tracked_open=5,
        shortlisted=5,
        estimated=5,
        scored=5,
        radar_surfaced=1,
        dropped_window=1,  # drops sum to 1, but scored - surfaced is 4
        dropped_win_multiple=0,
        dropped_win_probability=0,
        dropped_extreme_disagreement=0,
        categories={},
    )
    assert not bad.reconciles
