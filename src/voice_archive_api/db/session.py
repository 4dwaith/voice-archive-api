from collections.abc import Generator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from voice_archive_api.core.config import get_settings


def create_database_engine(database_url: str | URL) -> Engine:
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"},
    )


@lru_cache
def get_engine() -> Engine:
    return create_database_engine(get_settings().database_url)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_engine(), autoflush=False, expire_on_commit=False
    )


def get_db(
        factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> Generator[Session]:
    with factory() as s:
        yield s
