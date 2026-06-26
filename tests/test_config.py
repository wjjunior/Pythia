"""Config (Appendix A.3) boots in paper mode with zero secrets and fails fast."""

import pytest
from pydantic import SecretStr, ValidationError

from pythia.config import ExecutionModeError, Settings, ensure_paper_mode

_DB = "postgresql+psycopg://pythia:pythia@localhost:5434/pythia"


def test_boots_in_paper_mode_without_secrets() -> None:
    settings = Settings(_env_file=None, database_url=_DB)
    assert settings.mode == "paper"
    assert settings.openrouter_api_key is None
    assert settings.llm_enabled is False
    assert settings.use_live_venues is False
    ensure_paper_mode(settings)


def test_fails_fast_when_database_url_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_paper_mode_guard_rejects_live() -> None:
    settings = Settings(_env_file=None, database_url=_DB, mode="live")
    with pytest.raises(ExecutionModeError):
        ensure_paper_mode(settings)


def test_secret_fields_are_not_leaked_by_repr() -> None:
    values = (
        "sk-or-SECRET",
        "emb-SECRET",
        "meta-SECRET",
        "falcon-SECRET",
        "news-SECRET",
        "logfire-SECRET",
    )
    settings = Settings(
        _env_file=None,
        database_url=_DB,
        openrouter_api_key=SecretStr("sk-or-SECRET"),
        embedding_api_key=SecretStr("emb-SECRET"),
        metaculus_api_key=SecretStr("meta-SECRET"),
        falcon_api_token=SecretStr("falcon-SECRET"),
        newsapi_key=SecretStr("news-SECRET"),
        logfire_token=SecretStr("logfire-SECRET"),
    )
    text = repr(settings)
    for value in values:
        assert value not in text
    assert settings.openrouter_api_key is not None
    assert settings.openrouter_api_key.get_secret_value() == "sk-or-SECRET"


def test_cross_venue_sources_are_parsed() -> None:
    settings = Settings(
        _env_file=None,
        database_url=_DB,
        cross_venue_sources="kalshi, metaculus ,manifold",
    )
    assert settings.cross_venue_source_list == ["kalshi", "metaculus", "manifold"]
