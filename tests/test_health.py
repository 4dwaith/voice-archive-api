from pathlib import Path
from unittest.mock import Mock

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from voice_archive_api.core.config import Settings
from voice_archive_api.db.session import get_db
from voice_archive_api.main import create_app

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_health_returns_versioned_response(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "api_version": "v1"}


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
def test_api_documentation_is_reachable(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200


def test_openapi_contract_is_stable(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert schema["info"] == {"title": "Voice Archive API", "version": "0.1.0"}
    operation = schema["paths"]["/api/v1/health"]["get"]
    assert operation["operationId"] == "get_api_v1_health"
    response_schema = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert response_schema == {"$ref": "#/components/schemas/HealthResponse"}


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://localhost:5173",
        "https://127.0.0.1:5173",
    ],
)
def test_development_cors_origins_are_allowed(client: TestClient, origin: str) -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_ready_uses_isolated_postgres_database(
    client: TestClient, test_engine
) -> None:
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "api_version": "v1"}

    with test_engine.connect() as connection:
        assert connection.scalar(text("SELECT current_database()")).startswith(
            "va_test_"
        )
        assert "PostgreSQL" in connection.scalar(text("SELECT version()"))


def test_health_does_not_resolve_database_dependency() -> None:
    app = create_app(Settings(_env_file=None))
    unavailable_db = Mock(side_effect=AssertionError("Database must not be accessed"))
    app.dependency_overrides[get_db] = unavailable_db
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "api_version": "v1"}
    unavailable_db.assert_not_called()


def test_ready_returns_503_on_database_failure_and_recovers() -> None:
    app = create_app(Settings(_env_file=None))
    session = Mock(spec=Session)
    session.execute.side_effect = OperationalError(
        "SELECT 1", {}, Exception("private connection details")
    )
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app) as client:
        response = client.get("/api/v1/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable", "api_version": "v1"}
        assert str(session.execute.call_args.args[0]) == "SELECT 1"
        assert client.get("/api/v1/health").status_code == 200
        session.execute.side_effect = None
        assert client.get("/api/v1/ready").status_code == 200


def test_ready_openapi_contract(client: TestClient) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/ready"]["get"]
    assert operation["operationId"] == "get_api_v1_ready"
    assert "200" in operation["responses"]
    assert operation["responses"]["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/NotReadyResponse"
    }


def test_alembic_upgrades_empty_database_to_head_twice(test_database_url: str) -> None:
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", test_database_url.replace("%", "%%"))

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    engine = create_engine(test_database_url)
    try:
        assert inspect(engine).get_table_names() == ["alembic_version", "books"]
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert revision == "0001_book_metadata"
    finally:
        engine.dispose()
