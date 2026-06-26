"""Selection-harness tests (PYT-91, AC-S3): point-in-time scorecards, risk + agreement + n."""

import math
from datetime import UTC, datetime, timedelta

from pythia.common import TrackedTrader, TraderScorecard, TraderState
from pythia.smartmoney.metrics import SelectionPolicy
from pythia.smartmoney.selection import SelectionHarness, build_scorecard

_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_POLICY = SelectionPolicy(
    min_resolved=20,
    skew_hard_cap=-1.0,
    extreme_price_share_cap=0.5,
    w_edge=1.0,
    w_agree=1.0,
    w_cov=0.5,
    market_class_allow=frozenset({"politics", "sports"}),
    market_class_min_share=0.6,
)


def _trader(tid: str) -> TrackedTrader:
    return TrackedTrader(
        id=tid, wallet=f"0x{tid}", state=TraderState.TRIAL, added_at=_TS, state_since=_TS
    )


def test_build_scorecard_hard_caps_a_scalper() -> None:
    card = build_scorecard(
        scorecard_id="x",
        trader_id="t",
        asof=_TS,
        returns=[0.05] * 25 + [-1.2],
        categories=["politics"] * 26,
        entry_prices=[0.5] * 26,
        agree_rate=0.8,
        agreed_bpd=0.3,
        policy=_POLICY,
    )
    assert card.trader_score == float("-inf")  # scalper, despite the agreement
    assert card.skew is not None and card.skew < _POLICY.skew_hard_cap
    assert card.n_resolved == 26 and card.agreed_bpd == 0.3 and card.agree_rate == 0.8


def test_build_scorecard_is_finite_for_a_balanced_wallet() -> None:
    card = build_scorecard(
        scorecard_id="x",
        trader_id="t",
        asof=_TS,
        returns=[0.1, -0.05, 0.2, -0.1, 0.15, -0.08] * 5,
        categories=["politics"] * 30,
        entry_prices=[0.5] * 30,
        agree_rate=0.6,
        agreed_bpd=0.2,
        policy=_POLICY,
    )
    assert math.isfinite(card.trader_score)
    assert card.sortino is not None and card.max_drawdown is not None and card.roi is not None
    assert card.extreme_price_share == 0.0  # all entries mid-price


def test_build_scorecard_hard_caps_an_extreme_price_scalper() -> None:
    # Benign returns/skew, but every entry is at a near-certain price → convergence scalper (AC-S5).
    card = build_scorecard(
        scorecard_id="x",
        trader_id="t",
        asof=_TS,
        returns=[0.1, -0.05, 0.2, -0.1, 0.15, -0.08] * 5,
        categories=["politics"] * 30,
        entry_prices=[0.97] * 30,
        agree_rate=0.6,
        agreed_bpd=0.2,
        policy=_POLICY,
    )
    assert card.extreme_price_share == 1.0
    assert card.trader_score == float("-inf")  # never promotable despite a healthy return profile


def test_build_scorecard_with_no_resolutions_is_unpromotable() -> None:
    card = build_scorecard(
        scorecard_id="x",
        trader_id="t",
        asof=_TS,
        returns=[],
        categories=[],
        entry_prices=[],
        agree_rate=0.0,
        agreed_bpd=0.0,
        policy=_POLICY,
    )
    assert card.n_resolved == 0 and card.trader_score == float("-inf")


class _Returns:
    def __init__(self, data: list[tuple[datetime, float, str]]) -> None:
        self._data = data

    def resolved(
        self, trader: TrackedTrader, asof: datetime
    ) -> tuple[list[float], list[str], list[float]]:
        before = [(r, c) for (ts, r, c) in self._data if ts < asof]  # no lookahead
        return [r for r, _ in before], [c for _, c in before], [0.5] * len(before)


class _Agreement:
    def agreement(self, trader: TrackedTrader, asof: datetime) -> tuple[float, float]:
        return 0.6, 0.15


class _Store:
    def __init__(self) -> None:
        self.cards: list[TraderScorecard] = []

    def add(self, card: TraderScorecard) -> TraderScorecard:
        self.cards.append(card)
        return card


def test_harness_scorecards_are_point_in_time() -> None:
    data = [
        (_TS, 0.1, "politics"),
        (_TS + timedelta(days=1), -0.05, "politics"),
        (_TS + timedelta(days=3), 0.2, "sports"),
    ]
    harness = SelectionHarness(_Store(), _Returns(data), _Agreement(), _POLICY)
    trader = _trader("t1")

    early = harness.review([trader], asof=_TS + timedelta(days=2))[0]
    late = harness.review([trader], asof=_TS + timedelta(days=5))[0]

    assert early.n_resolved == 2 and late.n_resolved == 3  # n grows with asof — no lookahead
    assert early.asof == _TS + timedelta(days=2)
    assert (
        early.agree_rate == 0.6 and early.agreed_bpd == 0.15
    )  # pipeline-agreement carried (AC-S3)
    assert early.sortino is not None  # realized risk-adjusted edge included
