"""Engine and session wiring for the Postgres + pgvector store."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from pgvector.psycopg import register_vector
from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine

from pythia.config import get_settings


@lru_cache
def get_engine() -> Engine:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)

    # SQLAlchemy hands the raw DBAPI (psycopg) connection here; its type varies by driver.
    @event.listens_for(engine, "connect")
    def _register_vector(dbapi_connection: Any, _record: Any) -> None:
        register_vector(dbapi_connection)

    return engine


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
