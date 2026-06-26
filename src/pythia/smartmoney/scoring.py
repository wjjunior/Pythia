"""Concrete trader-scoring adapters (v2 §3.3, §4.2): the harness-validation engine.

Fills the two seams `SelectionHarness` (PYT-91) left as Protocols:

- `FalconTraderReturns` — per-resolution **realized returns** + categories for a wallet's increasing
  entries whose market resolved **before `asof`** (our `resolutions` are the no-lookahead gate and
  the category source); win/lose comes from Falcon's resolved `winning_outcome` (§4.2 from-Falcon).
- `PipelineAgreementEvaluator` — **pipeline agreement**: replay the live estimate+critic+score at
  each entry's point-in-time snapshot; `agree_rate` is how often we independently flagged +EV, and
  `agreed_bpd` is the dollar-weighted Brier *improvement vs the market price* on the agreed subset
  (positive ⇒ our forecast beat the market as a predictor — the headline calibration edge).

Both are strictly point-in-time: nothing observed after `asof` enters a scorecard (§3.5, AC-S3).
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session

from pythia.agents import DEFAULT_EVIDENCE_K, CriticAgent
from pythia.common import EstimationContext, EvidencePort, LLMPort, TrackedTrader
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


# Winsorise a single fill's dollar weight at this multiple of the median agreed weight (PYT-127), so
# one oversized lucky fill cannot dominate the promote-gate metric — the size twin of the PYT-116
# entry-price concentration guard. Domain robustness math, not operator-tunable.
_AGREED_BPD_WEIGHT_CAP_MULT = 5.0


def _winsorised_weights(weights: Sequence[float]) -> list[float]:
    cap = _AGREED_BPD_WEIGHT_CAP_MULT * statistics.median(weights)
    return [min(weight, cap) for weight in weights]


def agreement_metrics(samples: Sequence[AgreementSample]) -> tuple[float, float]:
    """(agree_rate, agreed_bpd): coverage, and dollar-weighted Brier improvement vs the market.

    Each agreed entry's dollar weight is winsorised at a multiple of the median agreed weight
    (PYT-127), so a single oversized lucky fill cannot swing ``agreed_bpd`` — a survivorship guard
    on the selection side. ``agree_rate`` is the unweighted coverage companion.
    """
    if not samples:
        return 0.0, 0.0
    agreed = [s for s in samples if s.agreed]
    agree_rate = len(agreed) / len(samples)
    if not agreed:
        return agree_rate, 0.0
    weights = _winsorised_weights([s.weight for s in agreed])
    total_weight = sum(weights)
    if total_weight <= 0.0:
        return agree_rate, 0.0
    improvement = sum(
        weight * (brier(s.market_price, s.outcome) - brier(s.p_model, s.outcome))
        for weight, s in zip(weights, agreed, strict=True)
    )
    return agree_rate, improvement / total_weight


class FalconTraderReturns:
    def __init__(self, session: Session, falcon: FalconPort) -> None:
        self._entries = TraderEntryRepo(session)
        self._resolutions = ResolutionRepository(session)
        self._markets = MarketRepository(session)
        self._falcon = falcon

    def resolved(
        self, trader: TrackedTrader, asof: datetime
    ) -> tuple[list[float], list[str], list[float]]:
        returns: list[float] = []
        categories: list[str] = []
        entry_prices: list[float] = []
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
            entry_prices.append(entry.entry_price)
        return returns, categories, entry_prices


class PipelineAgreementEvaluator:
    def __init__(
        self,
        session: Session,
        llm: LLMPort,
        evidence: EvidencePort,
        *,
        min_confidence: float,
        k: int = DEFAULT_EVIDENCE_K,
    ) -> None:
        self._entries = TraderEntryRepo(session)
        self._resolutions = ResolutionRepository(session)
        self._markets = MarketRepository(session)
        self._snapshots = SnapshotRepository(session)
        self._llm = llm
        self._critic = CriticAgent(llm)
        self._evidence = evidence
        self._min_confidence = min_confidence
        self._k = k

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
            # Replay the live estimate→critic→score path (§3.3): the mandatory critic lowers
            # confidence on lookahead/weak sources, so agreement counts only entries the system
            # would commit to. This is a DIRECTIONAL approximation of the live pipeline, not a PnL
            # replay (R-10): risk penalties are neutral (p_resol/p_liq/p_time/p_corr = 1.0), fee and
            # slippage are 0, and no recalibration map is applied. It answers "would we have
            # independently flagged +EV here?", which is all agree_rate/agreed_bpd need.
            evidence = self._evidence.retrieve(market.id, snapshot.ts, self._k)
            estimate = self._llm.estimate(
                EstimationContext(market=market, snapshot=snapshot, evidence=evidence)
            )
            critiqued = self._critic.review(estimate, evidence)
            score = build_score(
                critiqued,
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
                    agreed=score.edge_net > 0.0 and critiqued.confidence >= self._min_confidence,
                    p_model=critiqued.p_model,
                    market_price=snapshot.yes_price,
                    outcome=res.outcome,
                    weight=entry.size_usd if entry.size_usd and entry.size_usd > 0.0 else 1.0,
                )
            )
        return agreement_metrics(samples)


def build_selection_harness(
    session: Session,
    *,
    falcon: FalconPort,
    llm: LLMPort,
    evidence: EvidencePort,
    settings: Settings,
) -> SelectionHarness:
    """Compose the harness with the live returns + pipeline-agreement seams (§5.3)."""
    return SelectionHarness(
        TraderScorecardRepo(session),
        FalconTraderReturns(session, falcon),
        PipelineAgreementEvaluator(session, llm, evidence, min_confidence=settings.min_confidence),
        selection_policy(settings),
    )
