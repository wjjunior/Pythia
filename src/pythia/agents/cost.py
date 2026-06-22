"""Cost tracking and budget guards (§5.3): persist measured cost, halt runaway spend, cache results.

LLMPort is the single choke point where cost is measured. The BudgetGuard is the port's cost sink
(it persists every measured CostEvent, tagged with the run/market/estimate) and its pre-call gate
(it halts new calls once the per-run or daily cap is reached). The CachingLLMPort memoizes an
estimate by (market, snapshot, evidence_hash) so re-running an identical request never bills twice.
"""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

from pythia.common import (
    CostEvent,
    CriticVerdict,
    Estimate,
    EstimationContext,
    Evidence,
    LLMPort,
    Market,
    ResolutionProposal,
)
from pythia.common.logging import current_correlation_id
from pythia.db.repositories import CostRepository


class BudgetExceeded(RuntimeError):
    """A per-run or daily cost cap was reached; new model calls are halted."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def evidence_hash(evidence: list[Evidence]) -> str:
    digest = hashlib.sha256()
    for item in evidence:
        digest.update(item.id.encode())
        digest.update(b"\x00")
    return digest.hexdigest()


class BudgetGuard:
    def __init__(
        self,
        costs: CostRepository,
        *,
        run_cap: float,
        daily_cap: float,
        pipeline_run_id: str | None = None,
        market_id: str | None = None,
        estimate_id: str | None = None,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._costs = costs
        self._run_cap = run_cap
        self._daily_cap = daily_cap
        self._run_id = pipeline_run_id
        self._market_id = market_id
        self._estimate_id = estimate_id
        self._now = now
        self._run_spent = 0.0

    def record(self, event: CostEvent) -> None:
        # The pipeline binds the run id as the correlation (pipeline.py); fall back to it so cost
        # events are attributable per run (AC-8) even when the guard predates the run (PYT-48).
        self._costs.record(
            event,
            pipeline_run_id=self._run_id or current_correlation_id(),
            market_id=self._market_id,
            estimate_id=self._estimate_id,
        )
        self._run_spent += event.cost_usd

    def check(self) -> None:
        if self._run_spent >= self._run_cap:
            raise BudgetExceeded(
                f"per-run cost cap reached: {self._run_spent:.6f} >= {self._run_cap}"
            )
        start_of_day = self._now().replace(hour=0, minute=0, second=0, microsecond=0)
        if self._costs.daily_total(start_of_day) >= self._daily_cap:
            raise BudgetExceeded(f"daily cost budget reached: >= {self._daily_cap}")


class CachingLLMPort:
    """Memoize estimate by (market, snapshot, evidence_hash), scoped to this instance.

    An identical request is not re-billed within one pipeline run; the in-memory cache is not
    shared across runs (e.g. successive /api/run invocations).
    """

    def __init__(self, inner: LLMPort) -> None:
        self._inner = inner
        self._cache: dict[tuple[str, str, str], Estimate] = {}

    def estimate(self, ctx: EstimationContext) -> Estimate:
        key = (ctx.market.id, ctx.snapshot.id, evidence_hash(ctx.evidence))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        estimate = self._inner.estimate(ctx)
        self._cache[key] = estimate
        return estimate

    def critique(self, est: Estimate, evidence: list[Evidence]) -> CriticVerdict:
        return self._inner.critique(est, evidence)

    def grade_resolution(self, market: Market, evidence: list[Evidence]) -> ResolutionProposal:
        return self._inner.grade_resolution(market, evidence)
