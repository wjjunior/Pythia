"""Unit tests for evidence hashing and the estimate cache (no DB)."""

from datetime import UTC, datetime

from pythia.agents import CachingLLMPort, evidence_hash
from pythia.common import (
    CriticVerdict,
    Estimate,
    EstimationContext,
    Evidence,
    Market,
    MarketSnapshot,
)

_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _evidence(eid: str) -> Evidence:
    return Evidence(id=eid, market_id="m1", source_id="s1", observed_at=_TS, content="c")


def _ctx(evidence: list[Evidence] | None = None) -> EstimationContext:
    return EstimationContext(
        market=Market(id="m1", external_id="x", question="q", category="c", resolution_rule="r"),
        snapshot=MarketSnapshot(
            id="s1", market_id="m1", ts=_TS, yes_price=0.5, spread=0.02, volume=1.0, liquidity=1.0
        ),
        evidence=evidence if evidence is not None else [_evidence("e1")],
    )


def test_evidence_hash_is_deterministic_and_order_sensitive() -> None:
    a = [_evidence("e1"), _evidence("e2")]
    assert evidence_hash(a) == evidence_hash([_evidence("e1"), _evidence("e2")])
    assert evidence_hash(a) != evidence_hash([_evidence("e2"), _evidence("e1")])
    assert evidence_hash([]) == evidence_hash([])


class _CountingLLM:
    def __init__(self) -> None:
        self.estimate_calls = 0
        self.critique_calls = 0

    def estimate(self, ctx: EstimationContext) -> Estimate:
        self.estimate_calls += 1
        return Estimate(
            id=f"est-{self.estimate_calls}",
            market_id=ctx.market.id,
            snapshot_id=ctx.snapshot.id,
            p_model=0.5,
            confidence=0.5,
            lower=0.4,
            upper=0.6,
            rationale="r",
            evidence_ids=[item.id for item in ctx.evidence],
            model_name="fake",
        )

    def critique(self, est: Estimate, evidence: list[Evidence]) -> CriticVerdict:
        self.critique_calls += 1
        return CriticVerdict(
            adjusted_confidence=0.5, objections=[], lookahead_leak=False, notes=None
        )

    def discover(self, markets: list[Market], limit: int) -> list[str]:
        return []


def test_caching_memoizes_an_identical_estimate_request() -> None:
    inner = _CountingLLM()
    cache = CachingLLMPort(inner)
    first = cache.estimate(_ctx())
    second = cache.estimate(_ctx())
    assert inner.estimate_calls == 1
    assert first is second


def test_caching_recomputes_when_evidence_differs() -> None:
    inner = _CountingLLM()
    cache = CachingLLMPort(inner)
    cache.estimate(_ctx([_evidence("e1")]))
    cache.estimate(_ctx([_evidence("e9")]))
    assert inner.estimate_calls == 2


def test_caching_always_delegates_critique() -> None:
    inner = _CountingLLM()
    cache = CachingLLMPort(inner)
    estimate = cache.estimate(_ctx())
    cache.critique(estimate, [])
    assert inner.critique_calls == 1
