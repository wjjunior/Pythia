"""Pure roster-lifecycle tests (PYT-91/PYT-105, AC-S4): promote / demote / retire / bounded cap."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from pythia.common import TrackedTrader, TraderScorecard, TraderState
from pythia.smartmoney.roster import RosterPolicy, plan_transitions

_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_BENCH_TTL = timedelta(days=14)
_POLICY = RosterPolicy(
    max_traders=2,  # active deploy slots
    roster_max=3,  # total non-retired roster cap
    promote_score=1.0,
    promote_agree=0.1,
    demote_score=0.0,
    demote_agree=0.0,
    drawdown_floor=0.5,
    bench_ttl=_BENCH_TTL,
)


def _trader(tid: str, state: TraderState, *, since: datetime = _TS) -> TrackedTrader:
    return TrackedTrader(id=tid, wallet=f"0x{tid}", state=state, added_at=_TS, state_since=since)


def _card(
    tid: str, *, score: float, agree: float = 0.2, dd: float = 0.1, n: int = 30
) -> TraderScorecard:
    return TraderScorecard(
        id=f"sc-{tid}",
        trader_id=tid,
        asof=_TS,
        n_resolved=n,
        market_class_ok=True,
        trader_score=score,
        agreed_bpd=agree,
        max_drawdown=dd,
    )


def test_qualifying_trial_is_promoted_when_a_slot_is_open() -> None:
    transitions = plan_transitions(
        [_trader("t1", TraderState.TRIAL)], {"t1": _card("t1", score=2.0)}, _POLICY, asof=_TS
    )
    assert len(transitions) == 1
    assert transitions[0].from_state is TraderState.TRIAL
    assert transitions[0].to_state is TraderState.ACTIVE
    assert "promoted" in transitions[0].reason  # audited reason (AC-S4)


def test_trial_below_threshold_or_hard_floored_never_promotes() -> None:
    traders = [_trader("low", TraderState.TRIAL), _trader("capped", TraderState.TRIAL)]
    cards = {"low": _card("low", score=0.5), "capped": _card("capped", score=float("-inf"))}
    assert plan_transitions(traders, cards, _POLICY, asof=_TS) == []


def test_active_drifting_below_threshold_is_benched() -> None:
    transitions = plan_transitions(
        [_trader("a1", TraderState.ACTIVE)], {"a1": _card("a1", score=-0.5)}, _POLICY, asof=_TS
    )
    assert [t.to_state for t in transitions] == [TraderState.BENCHED]


def test_drawdown_floor_breach_retires_even_a_high_scorer() -> None:
    transitions = plan_transitions(
        [_trader("a1", TraderState.ACTIVE)],
        {"a1": _card("a1", score=2.0, dd=0.9)},
        _POLICY,
        asof=_TS,
    )
    assert transitions[0].to_state is TraderState.RETIRED
    assert "drawdown" in transitions[0].reason


def test_full_roster_evicts_the_weakest_active_for_a_stronger_trial() -> None:
    traders = [
        _trader("a1", TraderState.ACTIVE),
        _trader("a2", TraderState.ACTIVE),
        _trader("t1", TraderState.TRIAL),
    ]
    cards = {
        "a1": _card("a1", score=1.5),
        "a2": _card("a2", score=1.2),  # the weakest active
        "t1": _card("t1", score=3.0),  # a stronger trial
    }
    by_trader = {
        t.trader_id: t.to_state for t in plan_transitions(traders, cards, _POLICY, asof=_TS)
    }
    assert by_trader == {"a2": TraderState.BENCHED, "t1": TraderState.ACTIVE}


def test_full_roster_keeps_actives_when_the_trial_is_weaker() -> None:
    traders = [
        _trader("a1", TraderState.ACTIVE),
        _trader("a2", TraderState.ACTIVE),
        _trader("t1", TraderState.TRIAL),
    ]
    cards = {
        "a1": _card("a1", score=2.0),
        "a2": _card("a2", score=1.5),
        "t1": _card("t1", score=1.1),  # qualifies, but weaker than the weakest active
    }
    assert plan_transitions(traders, cards, _POLICY, asof=_TS) == []


def test_benched_past_the_ttl_without_recovery_is_retired() -> None:
    stale = _trader("b1", TraderState.BENCHED, since=_TS - _BENCH_TTL - timedelta(days=1))
    transitions = plan_transitions(
        [stale],
        {"b1": _card("b1", score=0.3)},
        _POLICY,
        asof=_TS,  # below promote_score
    )
    assert [t.to_state for t in transitions] == [TraderState.RETIRED]
    assert "benched" in transitions[0].reason  # audited staleness reason (AC-S4)


def test_benched_within_the_ttl_is_kept() -> None:
    fresh = _trader("b1", TraderState.BENCHED, since=_TS - timedelta(days=1))
    assert plan_transitions([fresh], {"b1": _card("b1", score=0.3)}, _POLICY, asof=_TS) == []


def test_recovered_stale_benched_is_promoted_not_retired() -> None:
    stale = _trader("b1", TraderState.BENCHED, since=_TS - _BENCH_TTL - timedelta(days=1))
    transitions = plan_transitions(
        [stale],
        {"b1": _card("b1", score=2.0)},
        _POLICY,
        asof=_TS,  # qualifies despite being stale
    )
    assert [t.to_state for t in transitions] == [TraderState.ACTIVE]
    assert "promoted" in transitions[0].reason


def test_roster_over_cap_retires_the_weakest_trial_or_benched() -> None:
    traders = [
        _trader("a1", TraderState.ACTIVE),
        _trader("t1", TraderState.TRIAL),
        _trader("t2", TraderState.TRIAL),
        _trader("b1", TraderState.BENCHED),
    ]  # 4 non-retired > roster_max (3); none qualify for promotion
    cards = {
        "a1": _card("a1", score=2.0),
        "t1": _card("t1", score=0.3),
        "t2": _card("t2", score=0.1),  # the weakest trial/benched
        "b1": _card("b1", score=0.2),
    }
    by_trader = {
        t.trader_id: t.to_state for t in plan_transitions(traders, cards, _POLICY, asof=_TS)
    }
    assert by_trader == {"t2": TraderState.RETIRED}  # only the overflow, only trial/benched


def test_over_cap_never_retires_an_active() -> None:
    tight = replace(_POLICY, roster_max=2)
    traders = [_trader(t, TraderState.ACTIVE) for t in ("a1", "a2", "a3")]  # 3 actives, cap 2
    cards = {
        "a1": _card("a1", score=2.0),
        "a2": _card("a2", score=1.8),
        "a3": _card("a3", score=1.5),
    }
    assert plan_transitions(traders, cards, tight, asof=_TS) == []  # actives are never evicted
