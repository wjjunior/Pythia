"""Unit tests for candidate enqueue (PYT-90, AC-S2): one per market, merged attribution, cap."""

from datetime import UTC, datetime, timedelta

from pythia.common import CandidateSignal, CandidateSignalStatus, TraderEntry
from pythia.smartmoney.candidate import enqueue_candidates

_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _entry(trader: str, market: str, ts: datetime) -> TraderEntry:
    return TraderEntry(
        id=f"{trader}:{market}:{ts.isoformat()}",
        trader_id=trader,
        external_market=market,
        side="YES",
        ts=ts,
        is_increase=True,
    )


class _FakeEvaluator:
    def __init__(self, *, returns_market: bool = True) -> None:
        self.calls: list[tuple[str, datetime, tuple[str, ...]]] = []
        self._returns_market = returns_market

    def evaluate(self, external_market: str, asof: datetime, trader_ids: list[str]) -> str | None:
        self.calls.append((external_market, asof, tuple(trader_ids)))
        return f"market:{external_market}" if self._returns_market else None


class _FakeStore:
    def __init__(self) -> None:
        self.signals: dict[str, CandidateSignal] = {}

    def get(self, signal_id: str) -> CandidateSignal | None:
        return self.signals.get(signal_id)

    def enqueue(self, signal: CandidateSignal) -> CandidateSignal:
        self.signals[signal.id] = signal
        return signal


def test_one_market_many_traders_is_evaluated_once_with_merged_attribution() -> None:
    evaluator = _FakeEvaluator()
    entries = [_entry("ta", "0xmkt", _TS), _entry("tb", "0xmkt", _TS + timedelta(minutes=5))]

    signals = enqueue_candidates(
        entries,
        store=_FakeStore(),
        evaluator=evaluator,
        daily_cap=10,
        evaluated_today=0,
        created_at=_TS,
    )

    assert len(evaluator.calls) == 1  # evaluated once despite two traders
    market, asof, traders = evaluator.calls[0]
    assert market == "0xmkt"
    assert asof == _TS + timedelta(minutes=5)  # asof = the latest triggering entry
    assert traders == ("ta", "tb")  # merged attribution
    assert len(signals) == 1
    assert signals[0].status is CandidateSignalStatus.EVALUATED
    assert signals[0].trader_ids == ["ta", "tb"]


def test_daily_cap_is_honored() -> None:
    evaluator = _FakeEvaluator()
    entries = [_entry("ta", "0xa", _TS), _entry("tb", "0xb", _TS)]  # two distinct markets

    signals = enqueue_candidates(
        entries,
        store=_FakeStore(),
        evaluator=evaluator,
        daily_cap=1,
        evaluated_today=0,
        created_at=_TS,
    )

    assert len(evaluator.calls) == 1  # cap of 1 → only one evaluation
    assert sorted(s.status.value for s in signals) == ["evaluated", "queued"]


def test_budget_already_spent_today_evaluates_nothing() -> None:
    evaluator = _FakeEvaluator()
    enqueue_candidates(
        [_entry("ta", "0xa", _TS)],
        store=_FakeStore(),
        evaluator=evaluator,
        daily_cap=1,
        evaluated_today=1,
        created_at=_TS,
    )
    assert evaluator.calls == []


def test_untracked_market_is_skipped_without_spending_budget() -> None:
    evaluator = _FakeEvaluator(returns_market=False)
    entries = [_entry("ta", "0xa", _TS), _entry("tb", "0xb", _TS)]

    signals = enqueue_candidates(
        entries,
        store=_FakeStore(),
        evaluator=evaluator,
        daily_cap=1,
        evaluated_today=0,
        created_at=_TS,
    )

    assert len(evaluator.calls) == 2  # both tried; neither consumed the cap (both skipped)
    assert all(s.status is CandidateSignalStatus.SKIPPED for s in signals)
    assert all(s.market_id is None for s in signals)


def test_enqueue_is_idempotent_per_market_asof() -> None:
    evaluator = _FakeEvaluator()
    store = _FakeStore()
    entries = [_entry("ta", "0xa", _TS)]
    enqueue_candidates(
        entries, store=store, evaluator=evaluator, daily_cap=5, evaluated_today=0, created_at=_TS
    )
    enqueue_candidates(  # same entries again
        entries, store=store, evaluator=evaluator, daily_cap=5, evaluated_today=0, created_at=_TS
    )
    assert len(evaluator.calls) == 1  # the second run sees the existing candidate → no re-eval
    assert len(store.signals) == 1
