from collections.abc import Generator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from voice_archive_api.core.config import get_settings


def create_database_engine(database_url: str) -> Engine:
    connect_args = (
        {"check_same_thread": False}
        if make_url(database_url).get_backend_name() == "sqlite"
        else {}
    )
    return create_engine(database_url, connect_args=connect_args)


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
) -> Generator[Session, None, None]:
    with factory() as s:
        yield s

