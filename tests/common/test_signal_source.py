from pythia.common import Estimate, SignalSource


def test_signal_source_members() -> None:
    assert {s.value for s in SignalSource} == {"radar", "smartmoney", "arb", "manual", "unknown"}


def test_estimate_requires_a_signal_source() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Estimate(  # type: ignore[call-arg]
            id="1",
            market_id="m",
            snapshot_id="s",
            p_model=0.5,
            confidence=0.5,
            lower=0.4,
            upper=0.6,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
        )  # no source
