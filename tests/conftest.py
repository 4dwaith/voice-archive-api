from collections.abc import Generator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url

from alembic import command
from voice_archive_api.core.application import Application
from voice_archive_api.core.config import Settings
from voice_archive_api.db.session import create_database_engine, get_db
from voice_archive_api.file_handling import LocalStorage
from voice_archive_api.main import create_app

pytest_plugins = ["tests.summary_helpers"]


@pytest.fixture(scope="session")
def test_admin_engine() -> Generator[Engine]:
    url = Settings().database_url
    if url.drivername != "postgresql+psycopg" or url.database != "voice_archive_test":
        pytest.fail("Tests require a PostgreSQL URL targeting the dedicated voice_archive_test database")
    engine = create_engine(url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5})
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def test_database_url(test_admin_engine: Engine) -> Generator[str]:
    # Every test owns a newly named database, including its Alembic history.
    # No truncation/drop operations ever target an application database.
    name = f"va_test_{uuid4().hex}"
    with test_admin_engine.connect() as connection:
        connection.connection.driver_connection.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name))
        )
    try:
        yield test_admin_engine.url.set(database=name).render_as_string(hide_password=False)
    finally:
        with test_admin_engine.connect() as connection:
            connection.connection.driver_connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def migration_config(test_database_url: str) -> Config:
    """Use the application's migration setup with this test's isolated database."""
    config = Config(Path(__file__).resolve().parents[1] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", test_database_url.replace("%", "%%"))
    return config


@pytest.fixture
def test_engine(test_database_url: str, migration_config: Config) -> Generator[Engine]:
    command.upgrade(migration_config, "head")
    engine = create_database_engine(test_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def application(test_database_url: str, test_engine: Engine, tmp_path: Path) -> Application:
    url = make_url(test_database_url)
    settings = Settings(
        environment="test",
        postgres_user=url.username,
        postgres_password=url.password,
        postgres_host=url.host,
        postgres_port=url.port,
        postgres_db=url.database,
        upload_root=tmp_path / "uploads",
        _env_file=None,
    )
    return create_app(settings)


@pytest.fixture
def file_storage(application: Application) -> LocalStorage:
    return LocalStorage(application.state.settings.upload_root)


@pytest.fixture
def client(application: Application) -> Generator[TestClient]:
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def schema_only_client() -> Generator[TestClient]:
    """Serve API documentation without provisioning a test database."""
    settings = Settings(
        environment="test",
        postgres_user="unused",
        postgres_password="unused",
        postgres_db="unused",
        _env_file=None,
    )
    app = create_app(settings)

    def unexpected_database_access():
        raise AssertionError("Schema requests must not access the database")

    app.dependency_overrides[get_db] = unexpected_database_access
    with TestClient(app) as test_client:
        yield test_client
