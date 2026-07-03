"""Unit tests for candidate enqueue (PYT-90, AC-S2): one per market, merged attribution, cap."""

from datetime import UTC, datetime, timedelta

from pythia.common import (
    CandidateSignal,
    CandidateSignalStatus,
    CandidateSource,
    TraderEntry,
)
from pythia.smartmoney.candidate import drain_queued, enqueue_candidates

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


_FAKE_ESTIMATE_ID = "est-fake-001"


class _FakeEvaluator:
    def __init__(self, *, returns_market: bool = True) -> None:
        self.calls: list[tuple[str, datetime, tuple[str, ...]]] = []
        self.ran: list[str] = []
        self._returns_market = returns_market

    def evaluate(
        self, external_market: str, asof: datetime, trader_ids: list[str]
    ) -> tuple[str, str | None] | None:
        self.calls.append((external_market, asof, tuple(trader_ids)))
        if self._returns_market:
            return (f"market:{external_market}", _FAKE_ESTIMATE_ID)
        return None

    def resolve(self, external_market: str) -> str | None:
        return f"market:{external_market}" if self._returns_market else None

    def run_market(self, market_id: str, asof: datetime, trader_ids: list[str]) -> str | None:
        self.ran.append(market_id)
        return _FAKE_ESTIMATE_ID if self._returns_market else None


class _FakeStore:
    def __init__(self) -> None:
        self.signals: dict[str, CandidateSignal] = {}
        self.estimate_links: dict[str, str] = {}  # signal_id → estimate_id

    def get(self, signal_id: str) -> CandidateSignal | None:
        return self.signals.get(signal_id)

    def enqueue(self, signal: CandidateSignal) -> CandidateSignal:
        self.signals[signal.id] = signal
        return signal

    def set_estimate(self, signal_id: str, estimate_id: str) -> None:
        self.estimate_links[signal_id] = estimate_id


class _FakeQueuedStore:
    def __init__(self, queued: list[CandidateSignal]) -> None:
        self._queued = list(queued)
        self.set: list[tuple[str, CandidateSignalStatus]] = []
        self.estimate_links: dict[str, str] = {}  # signal_id → estimate_id

    def list_by_status(self, status: CandidateSignalStatus) -> list[CandidateSignal]:
        return [s for s in self._queued if s.status is status]

    def set_status(self, signal_id: str, status: CandidateSignalStatus) -> CandidateSignal:
        self.set.append((signal_id, status))
        original = next(s for s in self._queued if s.id == signal_id)
        return original.model_copy(update={"status": status})

    def set_estimate(self, signal_id: str, estimate_id: str) -> None:
        self.estimate_links[signal_id] = estimate_id


def _queued(market: str, ts: datetime, *, market_id: str | None) -> CandidateSignal:
    return CandidateSignal(
        id=f"q:{market}",
        asof=ts,
        trader_ids=["ta"],
        source=CandidateSource.NEW_ENTRY,
        status=CandidateSignalStatus.QUEUED,
        created_at=ts,
        market_id=market_id,
    )


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


def test_over_cap_candidate_records_its_market_id_for_the_trigger_guard() -> None:
    # PYT-117/R-8: a QUEUED (over-cap) trigger must carry its market_id so the §3.2 evidence guard
    # sees it; otherwise the same flow could both trigger and become evidence (double-count).
    evaluator = _FakeEvaluator()
    entries = [_entry("ta", "0xa", _TS), _entry("tb", "0xb", _TS)]  # 0xa evaluated, 0xb over cap

    signals = enqueue_candidates(
        entries,
        store=_FakeStore(),
        evaluator=evaluator,
        daily_cap=1,
        evaluated_today=0,
        created_at=_TS,
    )

    queued = next(s for s in signals if s.status is CandidateSignalStatus.QUEUED)
    assert queued.market_id == "market:0xb"  # resolved despite being deferred
    assert evaluator.calls == [("0xa", _TS, ("ta",))]  # only the under-cap one ran the pipeline


def test_drain_processes_oldest_queued_first_under_budget() -> None:
    evaluator = _FakeEvaluator()
    store = _FakeQueuedStore(
        [
            _queued("0xa", _TS, market_id="m-a"),
            _queued("0xb", _TS + timedelta(minutes=1), market_id="m-b"),
        ]
    )

    drained = drain_queued(store=store, evaluator=evaluator, budget=1)

    assert [d.id for d in drained] == ["q:0xa"]  # oldest-first, bounded by budget
    assert store.set == [("q:0xa", CandidateSignalStatus.EVALUATED)]
    assert evaluator.ran == ["m-a"]  # only the oldest market was re-run; 0xb stays queued


def test_drain_skips_a_queued_candidate_without_a_resolved_market() -> None:
    evaluator = _FakeEvaluator()
    store = _FakeQueuedStore([_queued("0xa", _TS, market_id=None)])

    assert drain_queued(store=store, evaluator=evaluator, budget=5) == []
    assert evaluator.ran == []  # nothing to run for an untracked market
