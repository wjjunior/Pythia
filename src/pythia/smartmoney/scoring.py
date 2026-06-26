"""Concrete trader-scoring adapters (v2 §3.3, §4.2): the harness-validation engine.

Fills the two seams `SelectionHarness` (PYT-91) left as Protocols:

- `FalconTraderReturns` — per-resolution **realized returns** + categories for a wallet's increasing
  entries whose market resolved **before `asof`** (our `resolutions` are the no-lookahead gate and
  the category source); win/lose comes from Falcon's resolved `winning_outcome` (§4.2 from-Falcon).
- `PipelineAgreementEvaluator` — **pipeline agreement**: replay a lightweight estimate+score at each
  entry's point-in-time snapshot; `agree_rate` is how often we independently flagged +EV, and
  `agreed_bpd` is the dollar-weighted Brier *improvement vs the market price* on the agreed subset
  (positive ⇒ our forecast beat the market as a predictor — the headline calibration edge).

Both are strictly point-in-time: nothing observed after `asof` enters a scorecard (§3.5, AC-S3).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session

from pythia.agents import EstimationAgent
from pythia.common import TrackedTrader
from pythia.config import Settings
from pythia.db.repositories import MarketRepository, ResolutionRepository, SnapshotRepository
from pythia.db.smartmoney import TraderEntryRepo, TraderScorecardRepo
from pythia.eval import brier
from pythia.ingestion.falcon import FalconPort
from pythia.scoring import build_score
from pythia.smartmoney.selection import SelectionHarness, selection_policy

_NEUTRAL = 1.0


def position_return(*, won: bool, entry_price: float) -> float:
    """Per-resolution return of a $1 binary: a win pays (1-p)/p, a loss forfeits the whole stake."""
    return (1.0 - entry_price) / entry_price if won else -1.0


@dataclass(frozen=True, slots=True)
class AgreementSample:
    agreed: bool
    p_model: float
    market_price: float
    outcome: int
    weight: float


def agreement_metrics(samples: Sequence[AgreementSample]) -> tuple[float, float]:
    """(agree_rate, agreed_bpd): coverage, and dollar-weighted Brier improvement vs the market."""
    if not samples:
        return 0.0, 0.0
    agreed = [s for s in samples if s.agreed]
    agree_rate = len(agreed) / len(samples)
    total_weight = sum(s.weight for s in agreed)
    if not agreed or total_weight <= 0.0:
        return agree_rate, 0.0
    improvement = sum(
        s.weight * (brier(s.market_price, s.outcome) - brier(s.p_model, s.outcome)) for s in agreed
    )
    return agree_rate, improvement / total_weight


class FalconTraderReturns:
    def __init__(self, session: Session, falcon: FalconPort) -> None:
        self._entries = TraderEntryRepo(session)
        self._resolutions = ResolutionRepository(session)
        self._markets = MarketRepository(session)
        self._falcon = falcon

    def resolved(self, trader: TrackedTrader, asof: datetime) -> tuple[list[float], list[str]]:
        returns: list[float] = []
        categories: list[str] = []
        winning: dict[str, str | None] = {}
        for entry in self._entries.list_for_trader(trader.id, asof=asof):
            if not entry.is_increase or entry.market_id is None or entry.entry_price is None:
                continue
            if not 0.0 < entry.entry_price < 1.0:
                continue
            res = self._resolutions.get_for_market(entry.market_id)
            if res is None or res.ambiguous or res.resolved_at >= asof:
                continue  # unresolved, invalid (50/50), or not yet resolved at asof — no lookahead
            market = self._markets.get(entry.market_id)
            if market is None:
                continue
            if entry.external_market not in winning:
                falcon_market = self._falcon.get_market(entry.external_market)
                winning[entry.external_market] = (
                    falcon_market.winning_outcome if falcon_market is not None else None
                )
            win_outcome = winning[entry.external_market]
            if win_outcome is None:
                continue  # without the resolved label we cannot grade the trader's side
            returns.append(
                position_return(won=entry.side == win_outcome, entry_price=entry.entry_price)
            )
            categories.append(market.category)
        return returns, categories


class PipelineAgreementEvaluator:
    def __init__(self, session: Session, estimator: EstimationAgent) -> None:
        self._entries = TraderEntryRepo(session)
        self._resolutions = ResolutionRepository(session)
        self._markets = MarketRepository(session)
        self._snapshots = SnapshotRepository(session)
        self._estimator = estimator

    def agreement(self, trader: TrackedTrader, asof: datetime) -> tuple[float, float]:
        samples: list[AgreementSample] = []
        for entry in self._entries.list_for_trader(trader.id, asof=asof):
            if not entry.is_increase or entry.market_id is None:
                continue
            res = self._resolutions.get_for_market(entry.market_id)
            if res is None or res.ambiguous or res.resolved_at >= asof:
                continue
            market = self._markets.get(entry.market_id)
            if market is None:
                continue
            snapshot = self._snapshots.latest_for_market_asof(entry.market_id, asof=entry.ts)
            if snapshot is None:
                continue  # no point-in-time price at entry time — cannot replay
            estimate = self._estimator.estimate(market, snapshot)
            score = build_score(
                estimate,
                snapshot.yes_price,
                spread=snapshot.spread,
                fee=0.0,
                slippage=0.0,
                p_resol=_NEUTRAL,
                p_liq=_NEUTRAL,
                p_time=_NEUTRAL,
                p_corr=_NEUTRAL,
            )
            samples.append(
                AgreementSample(
                    agreed=score.edge_net > 0.0,
                    p_model=estimate.p_model,
                    market_price=snapshot.yes_price,
                    outcome=res.outcome,
                    weight=entry.size_usd if entry.size_usd and entry.size_usd > 0.0 else 1.0,
                )
            )
        return agreement_metrics(samples)


def build_selection_harness(
    session: Session, *, falcon: FalconPort, estimator: EstimationAgent, settings: Settings
) -> SelectionHarness:
    """Compose the harness with the live returns + pipeline-agreement seams (§5.3)."""
    return SelectionHarness(
        TraderScorecardRepo(session),
        FalconTraderReturns(session, falcon),
        PipelineAgreementEvaluator(session, estimator),
        selection_policy(settings),
    )
