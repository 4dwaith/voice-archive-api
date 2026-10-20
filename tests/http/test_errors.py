"""Global boundary behavior, including server failures outside route responses."""

import asyncio
import logging
from uuid import UUID, uuid4

import httpx
import pytest
from asgi_correlation_id import correlation_id
from fastapi.testclient import TestClient

from voice_archive_api.api.errors import HttpErrorResponse, InternalErrorResponse, ValidationErrorResponse
from voice_archive_api.core.config import Settings
from voice_archive_api.main import create_app

logger = logging.getLogger("voice_archive_api.test_probe")


@pytest.fixture
def boundary_client(tmp_path):
    app = create_app(
        Settings(
            environment="test",
            postgres_user="unused",
            postgres_password="unused",
            postgres_db="unused",
            upload_root=tmp_path,
            _env_file=None,
        )
    )

    @app.get("/test-failure")
    def fail():
        raise OSError("PRIVATE_EXCEPTION_TEXT")

    @app.get("/test-log")
    async def log_request():
        logger.info("probe-start")
        await asyncio.sleep(0)
        logger.info("probe-end")
        return {"id": correlation_id.get()}

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.mark.parametrize(
    "path,status,code,model",
    [
        ("/unknown-route", 404, "route_not_found", HttpErrorResponse),
        ("/test-failure", 500, "internal_error", InternalErrorResponse),
        ("/api/v1/books/invalid", 422, "validation_error", ValidationErrorResponse),
    ],
)
def test_errors_have_an_envelope_request_id_and_cors(boundary_client, caplog, path, status, code, model):
    with caplog.at_level(logging.INFO):
        response = boundary_client.get(path, headers={"Origin": "http://localhost:5173"})
    assert response.status_code == status
    body = model.model_validate(response.json())
    assert body.error.code == code
    assert body.error.message
    request_id = response.headers["X-Request-ID"]
    assert UUID(request_id).version == 4
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert "X-Request-ID" in response.headers["Access-Control-Expose-Headers"]
    assert "PRIVATE_EXCEPTION_TEXT" not in response.text
    if status == 500:
        records = [item for item in caplog.records if item.name == "voice_archive_api.api.errors"]
        assert len(records) == 1
        assert records[0].correlation_id == request_id
        assert "OSError" in records[0].getMessage()
        # No exception message or request body is included in application logs.
        assert "PRIVATE_EXCEPTION_TEXT" not in records[0].getMessage()


def test_method_error_retains_allow_header(boundary_client):
    response = boundary_client.post("/api/v1/health")
    assert response.status_code == 405
    assert response.headers["Allow"] == "GET"
    assert HttpErrorResponse.model_validate(response.json()).error.code == "method_not_allowed"


def test_request_id_validation_and_regeneration(boundary_client):
    supplied = str(uuid4())
    first = boundary_client.get("/api/v1/health", headers={"X-Request-ID": supplied})
    assert first.headers["X-Request-ID"] == supplied
    ids = [
        boundary_client.get("/api/v1/health", headers={"X-Request-ID": value}).headers["X-Request-ID"]
        for value in ("", "invalid-id", "")
    ]
    assert len(set(ids)) == 3
    assert all(UUID(value).version == 4 for value in ids)


def test_concurrent_request_logs_keep_their_own_ids(boundary_client, caplog):
    async def requests():
        transport = httpx.ASGITransport(app=boundary_client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await asyncio.gather(*(client.get("/test-log") for _ in range(4)))

    with caplog.at_level(logging.INFO):
        responses = asyncio.run(requests())
    ids = {response.headers["X-Request-ID"] for response in responses}
    assert len(ids) == 4
    for response in responses:
        assert response.json()["id"] == response.headers["X-Request-ID"]
    records = [item for item in caplog.records if item.name == logger.name]
    assert len(records) == 8
    for request_id in ids:
        assert [item.getMessage() for item in records if item.correlation_id == request_id] == [
            "probe-start",
            "probe-end",
        ]


def test_common_errors_and_request_id_headers_are_documented(schema_only_client):
    document = schema_only_client.get("/openapi.json").json()
    operation_ids = []
    for path in document["paths"].values():
        for operation in path.values():
            operation_ids.append(operation["operationId"])
            assert operation["responses"]["500"]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/InternalErrorResponse",
            }
            assert "405" in operation["responses"]
            for response in operation["responses"].values():
                assert "X-Request-ID" in response["headers"]
    assert len(set(operation_ids)) == len(operation_ids)
