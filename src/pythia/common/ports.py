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
    HeldPosition,
    Market,
    MarketSnapshot,
    PaperTrade,
    ResolutionProposal,
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
    def discover(self, markets: list[Market], limit: int) -> list[str]: ...
    def grade_resolution(self, market: Market, evidence: list[Evidence]) -> ResolutionProposal: ...


@runtime_checkable
class PaperLedgerPort(Protocol):
    def open(self, trade: PaperTrade) -> PaperTrade: ...
    def resolve(self, market_id: str, outcome: int, at: datetime) -> None: ...


@runtime_checkable
class PositionsPort(Protocol):
    """A wallet's currently-held positions at a venue (§4.5, PYT-157). Live calls are §8.6-gated."""

    def current_positions(self, wallet: str) -> list[HeldPosition]: ...


@runtime_checkable
class AuditPort(Protocol):
    def append(self, event: AuditEvent) -> None: ...


@runtime_checkable
class ExecutionPort(Protocol):
    """Real-money execution seam — deliberately UNIMPLEMENTED (§4.5, §3.2).

    No adapter implements this until the calibration gate clears (Brier(system) < Brier(market),
    stable over significant n). It exists so the gas / slippage / credentials conversation is forced
    at this boundary instead of leaking into the deterministic core; the design ends at this gate.
    """

    def execute(self, trade: PaperTrade) -> PaperTrade: ...
