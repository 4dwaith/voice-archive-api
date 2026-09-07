from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

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
    response_schema = operation["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
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
def test_development_cors_origins_are_allowed(
    client: TestClient, origin: str
) -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_health_uses_temporary_database(
    client: TestClient, test_database_url: str
) -> None:
    assert client.get("/api/v1/health").status_code == 200

    database_path = Path(test_database_url.removeprefix("sqlite:///"))
    assert database_path.exists()


def test_alembic_upgrades_empty_database_to_head_twice(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.sqlite3"
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        assert inspect(engine).get_table_names() == ["alembic_version"]
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert revision == "0001_backend_foundation"
    finally:
        engine.dispose()
