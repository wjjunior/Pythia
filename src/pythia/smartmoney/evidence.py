"""Optional smart-money flow as an estimation feature (v2 §3.2, §5.4).

Aggregate active-roster flow on a market may be exposed to the estimator behind
``SMART_MONEY_EVIDENCE_ENABLED`` — but **never** for a market the same signal triggered
(trigger ≠ evidence, §3.2), which would double-count one signal. The feature is appended as a
synthetic evidence item; it is not persisted and carries an unknown source, so it never enters the
deterministic risk/source-reliability path. Off by default — the wiring is a no-op when disabled.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from pythia.common import Evidence, EvidencePort, TraderEntry
from pythia.common.ids import deterministic_id

SMART_MONEY_SOURCE_ID = deterministic_id("source", "smart_money_flow")


def summarize_roster_flow(entries: Sequence[TraderEntry]) -> str | None:
    increases = [entry for entry in entries if entry.is_increase]
    if not increases:
        return None
    wallets = len({entry.trader_id for entry in increases})
    by_side = Counter(entry.side for entry in increases)
    breakdown = ", ".join(f"{count} {side}" for side, count in sorted(by_side.items()))
    sized = [entry.size_usd for entry in increases if entry.size_usd is not None]
    total = f"; total ${sum(sized):,.0f}" if sized else ""
    return f"{wallets} active-roster wallet(s) added positions ({breakdown}){total}"


def roster_flow_evidence(
    market_id: str,
    entries: Sequence[TraderEntry],
    *,
    asof: datetime,
    triggered: bool,
    enabled: bool,
) -> Evidence | None:
    # Trigger ≠ evidence (§3.2): never feed flow for a market the signal also triggered.
    if not enabled or triggered:
        return None
    summary = summarize_roster_flow(entries)
    if summary is None:
        return None
    return Evidence(
        id=deterministic_id("smart_money_flow", market_id, asof.isoformat()),
        market_id=market_id,
        source_id=SMART_MONEY_SOURCE_ID,
        observed_at=asof,
        content=f"Smart-money flow (as of {asof.isoformat()}): {summary}.",
    )


class RosterFlowProvider(Protocol):
    def active_on_market(self, market_id: str, *, asof: datetime) -> list[TraderEntry]: ...


class TriggeredMarkets(Protocol):
    def is_triggered(self, market_id: str) -> bool: ...


class SmartMoneyEvidencePort:
    """Decorates an ``EvidencePort``, appending active-roster flow as a synthetic evidence item."""

    def __init__(
        self,
        base: EvidencePort,
        *,
        flow: RosterFlowProvider,
        triggers: TriggeredMarkets,
        enabled: bool,
    ) -> None:
        self._base = base
        self._flow = flow
        self._triggers = triggers
        self._enabled = enabled

    def retrieve(self, market_id: str, asof: datetime, k: int) -> list[Evidence]:
        base = self._base.retrieve(market_id, asof, k)
        if not self._enabled:
            return base
        extra = roster_flow_evidence(
            market_id,
            self._flow.active_on_market(market_id, asof=asof),
            asof=asof,
            triggered=self._triggers.is_triggered(market_id),
            enabled=self._enabled,
        )
        return [*base, extra] if extra is not None else base
