from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from voice_archive_api.core.dependencies import get_app_state


def create_database_engine(database_url: str | URL) -> Engine:
    return create_engine(
        database_url,
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"},
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session_factory(request: Request) -> sessionmaker[Session]:
    return get_app_state(request).session_factory


def get_db(
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> Generator[Session]:
    with factory() as s:
        yield s
