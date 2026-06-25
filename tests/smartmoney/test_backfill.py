"""Unit tests for open-position backfill (PYT-94, AC-S8).

The load-bearing invariant is asof = now: an already-open position is judged at the *current*
price, never the trader's historical entry. The sweep also honours the market-class allow-list,
BACKFILL_MAX_POSITIONS, the daily cap, and dedupes against new-entry candidates in the bucket.
"""

from datetime import UTC, datetime

from pythia.common import (
    CandidateSignal,
    CandidateSignalStatus,
    CandidateSource,
    Market,
    TraderState,
)
from pythia.ingestion.falcon import OpenPosition
from pythia.smartmoney.backfill import run_backfill_sweep, should_backfill

_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_ALLOW = frozenset({"politics", "macro"})


def _market(external: str, category: str) -> Market:
    return Market(
        id=f"mkt:{external}",
        external_id=external,
        question="q",
        category=category,
        resolution_rule="r",
    )


def _position(external: str, *, avg_price: float = 0.9) -> OpenPosition:
    return OpenPosition(condition_id=external, outcome="Yes", size=100.0, avg_price=avg_price)


class _FakeMarkets:
    def __init__(self, markets: dict[str, Market]) -> None:
        self._markets = markets

    def get_by_external_id(self, external_id: str) -> Market | None:
        return self._markets.get(external_id)


class _FakeSignals:
    def __init__(self, *, triggered: set[str] | None = None) -> None:
        self.signals: dict[str, CandidateSignal] = {}
        self._triggered = triggered or set()

    def get(self, signal_id: str) -> CandidateSignal | None:
        return self.signals.get(signal_id)

    def enqueue(self, signal: CandidateSignal) -> CandidateSignal:
        self.signals[signal.id] = signal
        return signal

    def has_market_trigger(self, market_id: str, *, since: datetime | None = None) -> bool:
        return market_id in self._triggered


class _FakeEvaluator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime, tuple[str, ...]]] = []

    def evaluate(self, external_market: str, asof: datetime, trader_ids: list[str]) -> str | None:
        self.calls.append((external_market, asof, tuple(trader_ids)))
        return f"mkt:{external_market}"


def _sweep(
    positions: list[OpenPosition],
    markets: _FakeMarkets,
    *,
    signals: _FakeSignals | None = None,
    evaluator: _FakeEvaluator | None = None,
    max_positions: int = 20,
    daily_cap: int = 20,
    evaluated_today: int = 0,
) -> tuple[list[CandidateSignal], _FakeEvaluator]:
    ev = evaluator or _FakeEvaluator()
    sig = signals or _FakeSignals()
    out = run_backfill_sweep(
        "trader-1",
        positions,
        markets=markets,
        signals=sig,
        store=sig,
        evaluator=ev,
        allow=_ALLOW,
        max_positions=max_positions,
        daily_cap=daily_cap,
        evaluated_today=evaluated_today,
        now=_NOW,
    )
    return out, ev


def test_evaluates_at_now_not_the_traders_historical_entry() -> None:
    markets = _FakeMarkets({"0xa": _market("0xa", "politics")})
    out, ev = _sweep([_position("0xa", avg_price=0.92)], markets)

    assert [call[1] for call in ev.calls] == [_NOW]  # asof = now, never derived from avg_price
    assert len(out) == 1
    signal = out[0]
    assert signal.asof == _NOW
    assert signal.source is CandidateSource.OPEN_BACKFILL
    assert signal.status is CandidateSignalStatus.EVALUATED
    assert signal.market_id == "mkt:0xa"


def test_market_class_filter_excludes_off_allow_list() -> None:
    markets = _FakeMarkets({"0xa": _market("0xa", "politics"), "0xb": _market("0xb", "crypto")})
    out, ev = _sweep([_position("0xa"), _position("0xb")], markets)

    assert [s.market_id for s in out] == ["mkt:0xa"]  # crypto is off the allow-list (§3.4)
    assert len(ev.calls) == 1


def test_max_positions_caps_the_sweep() -> None:
    markets = _FakeMarkets({f"0x{i}": _market(f"0x{i}", "politics") for i in range(3)})
    out, _ = _sweep([_position(f"0x{i}") for i in range(3)], markets, max_positions=2)

    assert len(out) == 2


def test_daily_cap_defers_extra_positions() -> None:
    markets = _FakeMarkets({"0xa": _market("0xa", "politics"), "0xb": _market("0xb", "macro")})
    out, ev = _sweep([_position("0xa"), _position("0xb")], markets, daily_cap=1)

    assert len(ev.calls) == 1  # only one evaluation fits the cap
    statuses = {s.market_id: s.status for s in out}
    assert statuses["mkt:0xa"] is CandidateSignalStatus.EVALUATED
    assert statuses["mkt:0xb"] is CandidateSignalStatus.QUEUED  # deferred, still recorded


def test_dedupe_skips_a_market_already_triggered_in_bucket() -> None:
    markets = _FakeMarkets({"0xa": _market("0xa", "politics")})
    signals = _FakeSignals(triggered={"mkt:0xa"})  # a new_entry already owns this market-bucket
    out, ev = _sweep([_position("0xa")], markets, signals=signals)

    assert out == []  # new_entry wins over open_backfill (§5.5)
    assert ev.calls == []


def test_untracked_market_is_skipped() -> None:
    out, ev = _sweep([_position("0xunknown")], _FakeMarkets({}))

    assert out == []
    assert ev.calls == []


def test_same_market_twice_yields_one_candidate() -> None:
    markets = _FakeMarkets({"0xa": _market("0xa", "politics")})
    out, ev = _sweep([_position("0xa"), _position("0xa")], markets)

    assert len(out) == 1
    assert len(ev.calls) == 1


def test_should_backfill_keys_off_state_and_flags() -> None:
    assert should_backfill(TraderState.TRIAL, on_track=True, on_promote=False) is True
    assert should_backfill(TraderState.TRIAL, on_track=False, on_promote=False) is False
    assert should_backfill(TraderState.ACTIVE, on_track=False, on_promote=True) is True
    assert should_backfill(TraderState.ACTIVE, on_track=True, on_promote=False) is False
    assert should_backfill(TraderState.BENCHED, on_track=True, on_promote=True) is False
    assert should_backfill(TraderState.RETIRED, on_track=True, on_promote=True) is False
