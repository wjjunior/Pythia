"""Unit tests for the optional smart-money evidence feature (PYT-93, AC-S7).

The load-bearing invariant is trigger ≠ evidence (§3.2): flow is never exposed for a market the
same signal triggered, and the feature is off unless explicitly enabled.
"""

from datetime import UTC, datetime

from pythia.common import Evidence, TraderEntry
from pythia.smartmoney.evidence import (
    SMART_MONEY_SOURCE_ID,
    SmartMoneyEvidencePort,
    roster_flow_evidence,
    summarize_roster_flow,
)

_ASOF = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_MARKET = "mkt-1"


def _entry(
    trader: str,
    *,
    side: str = "YES",
    is_increase: bool = True,
    size: float | None = None,
) -> TraderEntry:
    return TraderEntry(
        id=f"{trader}:{side}",
        trader_id=trader,
        external_market="0xext",
        market_id=_MARKET,
        side=side,
        ts=_ASOF,
        is_increase=is_increase,
        size_usd=size,
    )


def test_triggered_market_never_gets_evidence() -> None:
    # Trigger ≠ evidence (§3.2): the same signal must not be both trigger and evidence.
    out = roster_flow_evidence(_MARKET, [_entry("ta")], asof=_ASOF, triggered=True, enabled=True)
    assert out is None


def test_disabled_is_a_noop() -> None:
    out = roster_flow_evidence(_MARKET, [_entry("ta")], asof=_ASOF, triggered=False, enabled=False)
    assert out is None


def test_enabled_untriggered_flow_produces_dated_evidence() -> None:
    out = roster_flow_evidence(
        _MARKET,
        [_entry("ta", side="YES"), _entry("tb", side="NO")],
        asof=_ASOF,
        triggered=False,
        enabled=True,
    )
    assert out is not None
    assert out.market_id == _MARKET
    assert out.source_id == SMART_MONEY_SOURCE_ID
    assert out.observed_at == _ASOF  # point-in-time: dated at asof, no lookahead
    assert "2 active-roster wallet(s)" in out.content


def test_no_increases_yields_nothing() -> None:
    out = roster_flow_evidence(
        _MARKET, [_entry("ta", is_increase=False)], asof=_ASOF, triggered=False, enabled=True
    )
    assert out is None


def test_summary_excludes_exits_and_counts_distinct_wallets() -> None:
    summary = summarize_roster_flow(
        [
            _entry("ta", side="YES", size=100.0),
            _entry("ta", side="YES", size=50.0),  # same wallet, still one
            _entry("tb", side="NO", size=200.0),
            _entry("tc", side="YES", is_increase=False),  # exit, excluded
        ]
    )
    assert summary is not None
    assert summary.startswith("2 active-roster wallet(s)")
    assert "2 YES" in summary and "1 NO" in summary
    assert "$350" in summary


class _FakeBase:
    def __init__(self, evidence: list[Evidence]) -> None:
        self._evidence = evidence

    def retrieve(self, market_id: str, asof: datetime, k: int) -> list[Evidence]:
        return list(self._evidence)


class _FakeFlow:
    def __init__(self, entries: list[TraderEntry]) -> None:
        self._entries = entries

    def active_on_market(self, market_id: str, *, asof: datetime) -> list[TraderEntry]:
        return self._entries


class _FakeTriggers:
    def __init__(self, *, triggered: bool) -> None:
        self._triggered = triggered

    def is_triggered(self, market_id: str) -> bool:
        return self._triggered


def _base_evidence() -> list[Evidence]:
    return [
        Evidence(
            id="ev-1",
            market_id=_MARKET,
            source_id="news",
            observed_at=_ASOF,
            content="base",
        )
    ]


def test_decorator_appends_flow_when_enabled_and_untriggered() -> None:
    port = SmartMoneyEvidencePort(
        _FakeBase(_base_evidence()),
        flow=_FakeFlow([_entry("ta")]),
        triggers=_FakeTriggers(triggered=False),
        enabled=True,
    )
    out = port.retrieve(_MARKET, _ASOF, 8)
    assert len(out) == 2
    assert out[-1].source_id == SMART_MONEY_SOURCE_ID


def test_decorator_passes_base_through_untouched_when_triggered() -> None:
    port = SmartMoneyEvidencePort(
        _FakeBase(_base_evidence()),
        flow=_FakeFlow([_entry("ta")]),
        triggers=_FakeTriggers(triggered=True),
        enabled=True,
    )
    out = port.retrieve(_MARKET, _ASOF, 8)
    assert [e.id for e in out] == ["ev-1"]


def test_decorator_is_noop_when_disabled() -> None:
    port = SmartMoneyEvidencePort(
        _FakeBase(_base_evidence()),
        flow=_FakeFlow([_entry("ta")]),
        triggers=_FakeTriggers(triggered=False),
        enabled=False,
    )
    out = port.retrieve(_MARKET, _ASOF, 8)
    assert [e.id for e in out] == ["ev-1"]
