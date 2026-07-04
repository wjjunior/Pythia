"""Config-boundary invariants (PYT-105): active deploy slots cannot outnumber the tracked roster."""

import pytest
from pydantic import ValidationError

from pythia.config import Settings

_DB = "postgresql+psycopg://x/y"


def test_defaults_keep_active_slots_within_the_tracked_roster() -> None:
    settings = Settings(_env_file=None, database_url=_DB)
    assert settings.active_traders_max <= settings.tracked_traders_max


def test_active_slots_exceeding_the_tracked_roster_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=_DB, active_traders_max=11, tracked_traders_max=10)


def test_negative_roster_caps_and_ttl_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=_DB, tracked_traders_max=-1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=_DB, trader_bench_ttl_seconds=-1)


def test_extreme_price_share_cap_outside_the_unit_interval_is_rejected() -> None:
    # A cap > 1 silently disables the scalper guard; < 0 hard-caps every wallet (PYT-116).
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=_DB, trader_extreme_price_share_cap=1.5)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=_DB, trader_extreme_price_share_cap=-0.1)


def test_liquidity_ref_must_be_positive() -> None:
    # p_liq divides by liquidity_ref; 0 or negative would crash/invert scoring (PYT-189).
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=_DB, liquidity_ref=0.0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=_DB, liquidity_ref=-1.0)


def test_trade_costs_reject_negative_values() -> None:
    # A negative fee/slippage would invert the cost and inflate scores/fills (PYT-189).
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=_DB, trade_fee=-0.01)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=_DB, trade_slippage=-0.01)


def test_falcon_agent_id_overrides_default_to_empty() -> None:
    assert Settings(_env_file=None, database_url=_DB).falcon_agent_id_overrides == {}


def test_falcon_agent_id_overrides_parse_a_dataset_id_map() -> None:
    # PYT-131: lets the operator correct a rotated Falcon agent_id from .env without a redeploy.
    settings = Settings(
        _env_file=None, database_url=_DB, falcon_agent_ids="trader_trades:556, market:574"
    )
    assert settings.falcon_agent_id_overrides == {"trader_trades": 556, "market": 574}


def test_falcon_agent_ids_missing_colon_is_rejected() -> None:
    settings = Settings(_env_file=None, database_url=_DB, falcon_agent_ids="trader_trades")
    with pytest.raises(ValueError, match="expected 'dataset:id'"):
        _ = settings.falcon_agent_id_overrides


def test_swapped_cohort_price_band_is_rejected() -> None:
    # PYT-167: a min > max band would silently empty the midband (shortlist bias + MIDBAND cohort).
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url=_DB,
            eval_cohort_price_min=0.85,
            eval_cohort_price_max=0.10,
        )


def test_default_cohort_price_band_is_ordered() -> None:
    settings = Settings(_env_file=None, database_url=_DB)
    assert settings.eval_cohort_price_min <= settings.eval_cohort_price_max
