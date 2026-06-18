"""Ports (§4.5): the seams every adapter implements. Zero infrastructure.

`EvidencePort.retrieve` takes `asof` as a required parameter so the point-in-time
guarantee is part of the contract, not an implementation accident.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from pythia.common.types import (
    AuditEvent,
    CriticVerdict,
    Estimate,
    EstimationContext,
    Evidence,
    Market,
    MarketSnapshot,
    PaperTrade,
)


@runtime_checkable
class MarketDataPort(Protocol):
    def list_markets(self, **filters: object) -> list[Market]: ...
    def snapshot(self, external_id: str) -> MarketSnapshot: ...


@runtime_checkable
class EvidencePort(Protocol):
    def retrieve(self, market_id: str, asof: datetime, k: int) -> list[Evidence]: ...


@runtime_checkable
class EmbeddingPort(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class LLMPort(Protocol):
    def estimate(self, ctx: EstimationContext) -> Estimate: ...
    def critique(self, est: Estimate, evidence: list[Evidence]) -> CriticVerdict: ...


@runtime_checkable
class PaperLedgerPort(Protocol):
    def open(self, trade: PaperTrade) -> PaperTrade: ...
    def resolve(self, market_id: str, outcome: int, at: datetime) -> None: ...


@runtime_checkable
class AuditPort(Protocol):
    def append(self, event: AuditEvent) -> None: ...
