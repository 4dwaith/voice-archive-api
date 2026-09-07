from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from voice_archive_api.core.config import Settings
from voice_archive_api.db.session import create_database_engine, get_db
from voice_archive_api.main import create_app


@pytest.fixture
def test_database_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}"


@pytest.fixture
def test_engine(test_database_url: str) -> Generator[Engine, None, None]:
    engine = create_database_engine(test_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def application(test_database_url: str, test_engine: Engine) -> FastAPI:
    settings = Settings(
        environment="test",
        database_url=test_database_url,
        upload_root=Path("unused-in-phase-01"),
        _env_file=None,
    )
    app = create_app(settings)
    test_session_factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    def override_get_db() -> Generator[Session, None, None]:
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest.fixture
def client(application: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(application) as test_client:
        yield test_client

