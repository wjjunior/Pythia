"""Selection harness (v2 §4.2, §5.3): point-in-time scorecards from returns + pipeline-agreement.

``build_scorecard`` is pure. ``SelectionHarness`` reviews each wallet at an ``asof`` — sourcing its
per-resolution returns/categories and its pipeline-agreement through seams that must only use data
resolved **before** ``asof`` (no lookahead, §3.5) — and persists the scorecard. The real seams wire
Falcon P&L (returns) and a held-out replay of the wallet's resolved entries through the v1 pipeline
(``agree_rate``, ``agreed_bpd``); both stay behind the Protocols so the harness is testable offline.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

import numpy as np

from pythia.common import TrackedTrader, TraderScorecard
from pythia.common.ids import deterministic_id
from pythia.config import Settings
from pythia.smartmoney.metrics import (
    SelectionPolicy,
    extreme_price_share,
    market_class_ok,
    max_drawdown,
    risk_adj_edge,
    skewness,
    sortino,
    trader_score,
)
from pythia.smartmoney.roster import RosterPolicy

# Score weights (§4.2) — domain math, not operator-tunable.
W_EDGE = 1.0
W_AGREE = 1.0
W_COV = 0.5
SKEW_HARD_CAP = -1.0
MARKET_CLASS_MIN_SHARE = 0.6


def selection_policy(settings: Settings) -> SelectionPolicy:
    allow = frozenset(c.strip() for c in settings.market_class_allow.split(",") if c.strip())
    return SelectionPolicy(
        min_resolved=settings.trader_trial_min_resolved,
        skew_hard_cap=SKEW_HARD_CAP,
        extreme_price_share_cap=settings.trader_extreme_price_share_cap,
        w_edge=W_EDGE,
        w_agree=W_AGREE,
        w_cov=W_COV,
        market_class_allow=allow,
        market_class_min_share=MARKET_CLASS_MIN_SHARE,
    )


def roster_policy(settings: Settings) -> RosterPolicy:
    return RosterPolicy(
        max_traders=settings.active_traders_max,
        roster_max=settings.tracked_traders_max,
        promote_score=settings.trader_promote_score,
        promote_agree=settings.trader_promote_agree,
        demote_score=settings.trader_demote_score,
        demote_agree=settings.trader_demote_agree,
        drawdown_floor=settings.trader_drawdown_floor,
        bench_ttl=timedelta(seconds=settings.trader_bench_ttl_seconds),
    )


def build_scorecard(
    *,
    scorecard_id: str,
    trader_id: str,
    asof: datetime,
    returns: Sequence[float],
    categories: Sequence[str],
    entry_prices: Sequence[float],
    agree_rate: float,
    agreed_bpd: float,
    policy: SelectionPolicy,
) -> TraderScorecard:
    """Pure: a point-in-time scorecard from per-resolution returns, categories, entry prices, and
    agreement."""
    n_resolved = len(returns)
    class_ok = market_class_ok(categories, policy)
    share = extreme_price_share(entry_prices)
    if n_resolved == 0:  # nothing resolved yet → never promotable, but still a (visible) scorecard
        return TraderScorecard(
            id=scorecard_id,
            trader_id=trader_id,
            asof=asof,
            n_resolved=0,
            market_class_ok=class_ok,
            extreme_price_share=share,
            trader_score=float("-inf"),
        )
    edge = risk_adj_edge(returns)
    skew = skewness(returns)
    return TraderScorecard(
        id=scorecard_id,
        trader_id=trader_id,
        asof=asof,
        n_resolved=n_resolved,
        roi=float(np.mean(returns)),
        risk_adj_edge=edge,
        sortino=sortino(returns),
        skew=skew,
        max_drawdown=max_drawdown(returns),
        agree_rate=agree_rate,
        agreed_bpd=agreed_bpd,
        market_class_ok=class_ok,
        extreme_price_share=share,
        trader_score=trader_score(
            risk_adj_edge=edge,
            agreed_bpd=agreed_bpd,
            coverage=agree_rate,
            n_resolved=n_resolved,
            market_class_ok=class_ok,
            skew=skew,
            extreme_price_share=share,
            policy=policy,
        ),
    )


class TraderReturns(Protocol):
    def resolved(
        self, trader: TrackedTrader, asof: datetime
    ) -> tuple[list[float], list[str], list[float]]:
        """Per-resolution returns, categories and entry prices for entries resolved before asof."""
        ...


class AgreementEvaluator(Protocol):
    def agreement(self, trader: TrackedTrader, asof: datetime) -> tuple[float, float]:
        """(agree_rate, agreed_bpd): replay resolved entries through the pipeline, point-in-time."""
        ...


class ScorecardStore(Protocol):
    def add(self, card: TraderScorecard) -> TraderScorecard: ...


class SelectionHarness:
    def __init__(
        self,
        scorecards: ScorecardStore,
        returns: TraderReturns,
        agreement: AgreementEvaluator,
        policy: SelectionPolicy,
    ) -> None:
        self._scorecards = scorecards
        self._returns = returns
        self._agreement = agreement
        self._policy = policy

    def review(self, traders: Sequence[TrackedTrader], *, asof: datetime) -> list[TraderScorecard]:
        """Write a point-in-time scorecard for each wallet; return them (newest per wallet)."""
        cards: list[TraderScorecard] = []
        for trader in traders:
            returns, categories, entry_prices = self._returns.resolved(trader, asof)
            agree_rate, agreed_bpd = self._agreement.agreement(trader, asof)
            card = build_scorecard(
                scorecard_id=deterministic_id("scorecard", trader.id, asof.isoformat()),
                trader_id=trader.id,
                asof=asof,
                returns=returns,
                categories=categories,
                entry_prices=entry_prices,
                agree_rate=agree_rate,
                agreed_bpd=agreed_bpd,
                policy=self._policy,
            )
            cards.append(self._scorecards.add(card))
        return cards
