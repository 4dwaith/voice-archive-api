"""Global boundary behavior, including server failures outside route responses."""

import asyncio
import logging
from uuid import UUID, uuid4

import httpx
import pytest
from asgi_correlation_id import correlation_id
from fastapi.testclient import TestClient

from voice_archive_api.api.schemas.errors import HttpErrorResponse, InternalErrorResponse, ValidationErrorResponse
from voice_archive_api.api.v1.file_uploads import InvalidMultipart
from voice_archive_api.core.config import Settings
from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.file_handling import FileTooLarge
from voice_archive_api.main import create_app
from voice_archive_api.services.books.service import BookNotFound, DuplicateBook
from voice_archive_api.services.files.covers import InvalidImage, UnsupportedMediaType
from voice_archive_api.services.files.pdfs import EncryptedPdfNotSupported, InvalidPdf
from voice_archive_api.services.files.service import FileAlreadyLinked, FileKindMismatch, FileNotFound

logger = logging.getLogger("voice_archive_api.test_probe")

EXPECTED_FAILURES = [
    (BookNotFound, 404, "book_not_found"),
    (DuplicateBook, 409, "duplicate_book"),
    (FileNotFound, 404, "file_not_found"),
    (FileAlreadyLinked, 409, "file_already_linked"),
    (FileKindMismatch, 422, "file_kind_mismatch"),
    (InvalidImage, 422, "invalid_image"),
    (UnsupportedMediaType, 415, "unsupported_media_type"),
    (InvalidPdf, 422, "invalid_pdf"),
    (EncryptedPdfNotSupported, 422, "encrypted_pdf_not_supported"),
    (FileTooLarge, 413, "file_too_large"),
    (InvalidMultipart, 400, "invalid_multipart"),
]


class MissingImportedFile(FileNotFound):
    """A specialized error should inherit its parent's public representation."""


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

    @app.get("/test-application-failure/{case}")
    def fail_application(case: int):
        error, _, _ = EXPECTED_FAILURES[case]
        raise error("PRIVATE_EXCEPTION_TEXT")

    @app.get("/test-inherited-failure")
    def fail_inherited():
        raise MissingImportedFile("PRIVATE_EXCEPTION_TEXT")

    @app.get("/test-unregistered-failure")
    def fail_unregistered():
        class UnregisteredError(ApplicationError):
            code = "private_unregistered_code"
            message = "PRIVATE_EXCEPTION_TEXT"

        raise UnregisteredError

    @app.get("/test-log")
    async def log_request():
        logger.info("probe-start")
        await asyncio.sleep(0)
        logger.info("probe-end")
        return {"id": correlation_id.get()}

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.mark.parametrize("case,expected", list(enumerate(EXPECTED_FAILURES)))
def test_application_errors_keep_public_status_codes_and_messages(boundary_client, case, expected):
    error, status, code = expected
    response = boundary_client.get(f"/test-application-failure/{case}")
    assert response.status_code == status
    assert response.json() == {"error": {"code": code, "message": error.message}}
    assert "PRIVATE_EXCEPTION_TEXT" not in response.text


def test_application_error_handler_accepts_specialized_subclasses(boundary_client):
    response = boundary_client.get("/test-inherited-failure")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "file_not_found", "message": FileNotFound.message}}
    assert "PRIVATE_EXCEPTION_TEXT" not in response.text


def test_unregistered_application_error_uses_generic_json_envelope(boundary_client, caplog):
    response = boundary_client.get("/test-unregistered-failure", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"error": {"code": "internal_error", "message": "An unexpected error occurred"}}
    assert response.headers["X-Request-ID"]
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert "private_unregistered_code" not in response.text
    assert "PRIVATE_EXCEPTION_TEXT" not in response.text
    assert "UnregisteredError" in caplog.text
    assert "PRIVATE_EXCEPTION_TEXT" not in caplog.text


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
