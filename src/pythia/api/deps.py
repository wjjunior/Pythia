"""FastAPI dependencies: typed settings, a request-scoped session, and the SSE broadcaster."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlmodel import Session

from pythia.api.events import EventBroadcaster
from pythia.config import Settings, get_settings
from pythia.db.session import session_scope


def settings_dependency() -> Settings:
    return get_settings()


def get_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session


def get_broadcaster(request: Request) -> EventBroadcaster:
    broadcaster: EventBroadcaster = request.app.state.broadcaster
    return broadcaster


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(settings_dependency)]
BroadcasterDep = Annotated[EventBroadcaster, Depends(get_broadcaster)]
