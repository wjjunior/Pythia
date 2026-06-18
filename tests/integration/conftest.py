"""Integration fixtures: a real Postgres+pgvector via testcontainers, migrated with Alembic."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlmodel import Session
from testcontainers.postgres import PostgresContainer

from pythia.config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def migrated_engine() -> Iterator[Engine]:
    with PostgresContainer("pgvector/pgvector:pg18", driver="psycopg") as postgres:
        url = postgres.get_connection_url()
        os.environ["DATABASE_URL"] = url
        get_settings.cache_clear()
        command.upgrade(Config(str(_REPO_ROOT / "alembic.ini")), "head")
        engine = create_engine(url)
        try:
            yield engine
        finally:
            engine.dispose()
            os.environ.pop("DATABASE_URL", None)
            get_settings.cache_clear()


@pytest.fixture
def session(migrated_engine: Engine) -> Iterator[Session]:
    with Session(migrated_engine) as db_session:
        yield db_session
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE markets, market_snapshots, sources CASCADE"))
