import pytest

from .helpers import assert_binary_response_contract, assert_multipart_file_body_contract, assert_operation_contract


@pytest.mark.parametrize(
    "path,method,operation_id,responses",
    [
        (
            "/api/v1/cover-files",
            "post",
            "create_api_v1_cover_file",
            {
                "201": "CoverFileResponse",
                "400": "FileUploadErrorResponse",
                "413": "FileUploadErrorResponse",
                "415": "CoverFileErrorResponse",
                "422": ["CoverFileErrorResponse", "ValidationErrorResponse"],
            },
        ),
        (
            "/api/v1/cover-files/{file_id}", "get", "get_api_v1_cover_file",
            {"404": "CoverFileErrorResponse", "416": None, "422": "ValidationErrorResponse"},
        ),
        (
            "/api/v1/cover-files/{file_id}", "head", "head_api_v1_cover_file",
            {"200": None, "206": None, "400": None, "404": "CoverFileErrorResponse", "416": None, "422": "ValidationErrorResponse"},
        ),
        (
            "/api/v1/cover-files/{file_id}", "delete", "delete_api_v1_cover_file",
            {"204": None, "404": "CoverFileErrorResponse", "409": "CoverFileErrorResponse", "422": "ValidationErrorResponse"},
        ),
    ],
    ids=["create", "retrieve", "headers", "delete"],
)
def test_cover_file_operation_contracts(openapi_document, path, method, operation_id, responses):
    assert_operation_contract(openapi_document, path, method, operation_id, responses)


@pytest.mark.parametrize(
    "path,method,operation_id,responses",
    [
        (
            "/api/v1/source-pdf-files",
            "post",
            "create_api_v1_source_pdf_file",
            {
                "201": "SourcePdfFileResponse",
                "400": "FileUploadErrorResponse",
                "413": "FileUploadErrorResponse",
                "422": ["SourcePdfFileErrorResponse", "ValidationErrorResponse"],
            },
        ),
        (
            "/api/v1/source-pdf-files/{file_id}", "get", "get_api_v1_source_pdf_file",
            {"404": "SourcePdfFileErrorResponse", "416": None, "422": "ValidationErrorResponse"},
        ),
        (
            "/api/v1/source-pdf-files/{file_id}", "head", "head_api_v1_source_pdf_file",
            {"200": None, "206": None, "400": None, "404": "SourcePdfFileErrorResponse", "416": None, "422": "ValidationErrorResponse"},
        ),
        (
            "/api/v1/source-pdf-files/{file_id}", "delete", "delete_api_v1_source_pdf_file",
            {"204": None, "404": "SourcePdfFileErrorResponse", "409": "SourcePdfFileErrorResponse", "422": "ValidationErrorResponse"},
        ),
    ],
    ids=["create", "retrieve", "headers", "delete"],
)
def test_source_pdf_file_operation_contracts(openapi_document, path, method, operation_id, responses):
    assert_operation_contract(openapi_document, path, method, operation_id, responses)


@pytest.mark.parametrize(
    "path,method,file_field",
    [
        ("/api/v1/cover-files", "post", "file"),
        ("/api/v1/source-pdf-files", "post", "file"),
    ],
    ids=["cover", "source-pdf"],
)
def test_upload_body_contract_requires_a_multipart_file(openapi_document, path, method, file_field):
    assert_multipart_file_body_contract(openapi_document, path, method, file_field)


@pytest.mark.parametrize("model,fields", [
    ("CoverFileResponse", {"id", "byte_size", "width", "height", "created_at"}),
    ("SourcePdfFileResponse", {"id", "byte_size", "page_count", "created_at"}),
])
def test_upload_response_contract_exposes_only_public_file_details(openapi_document, model, fields):
    schema = openapi_document["components"]["schemas"][model]
    assert set(schema["properties"]) == fields
    assert set(schema["required"]) == fields


@pytest.mark.parametrize("path", ["/api/v1/cover-files", "/api/v1/source-pdf-files"])
@pytest.mark.parametrize("case", ["missing", "wrong-name", "multiple", "text-field", "missing-boundary", "malformed", "not-multipart"])
def test_invalid_upload_request_has_a_documented_error(client, file_storage, path, case):
    if case == "missing":
        response = client.post(path, content=b"--empty--\r\n", headers={
            "Content-Type": "multipart/form-data; boundary=empty",
        })
    elif case == "wrong-name":
        response = client.post(path, files={"other": ("file.bin", b"data")})
    elif case == "multiple":
        response = client.post(path, files=[("file", ("a", b"a")), ("file", ("b", b"b"))])
    elif case == "text-field":
        response = client.post(path, files={"file": ("a", b"a")}, data={"book_id": "1"})
    elif case == "missing-boundary":
        response = client.post(path, content=b"data", headers={"Content-Type": "multipart/form-data"})
    elif case == "malformed":
        response = client.post(path, content=b"not the boundary", headers={
            "Content-Type": "multipart/form-data; boundary=expected",
        })
    else:
        response = client.post(path, content=b"data", headers={"Content-Type": "application/pdf"})

    if case in {"missing", "wrong-name"}:
        assert response.status_code == 422, response.text
        body = response.json()
        assert body["error"]["code"] == "validation_error"
        assert any(issue["location"] == ["body", "file"] for issue in body["issues"])
    else:
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "invalid_multipart"
    assert not any(path.is_file() for path in file_storage.root.rglob("*"))


@pytest.mark.parametrize("path,media_type", [
    ("/api/v1/cover-files/{file_id}", "image/webp"),
    ("/api/v1/source-pdf-files/{file_id}", "application/pdf"),
])
@pytest.mark.parametrize("status", ["200", "206"])
def test_file_delivery_binary_contracts(openapi_document, path, media_type, status):
    assert_binary_response_contract(
        openapi_document, path, "get", status, media_type,
        additional_media_types=("multipart/byteranges",) if status == "206" else (),
    )
    assert "requestBody" not in openapi_document["paths"][path]["get"]


@pytest.mark.parametrize("path", ["/api/v1/cover-files", "/api/v1/source-pdf-files"])
@pytest.mark.parametrize("method", ["get", "head", "delete"])
@pytest.mark.parametrize("file_id", ["0", "invalid"])
def test_invalid_delivery_id_has_a_documented_error(client, path, method, file_id):
    response = client.request(method, f"{path}/{file_id}")
    assert response.status_code == 422
    if method != "head":
        assert response.json()["error"]["code"] == "validation_error"
    else:
        assert response.content == b""


@pytest.mark.parametrize("path", ["/api/v1/cover-files/{file_id}", "/api/v1/source-pdf-files/{file_id}"])
def test_delivery_range_errors_document_native_text_response(openapi_document, path):
    responses = openapi_document["paths"][path]["get"]["responses"]
    assert responses["400"]["content"] == {"text/plain": {"schema": {"type": "string"}}}
    assert "content" not in responses["416"]
    assert "Content-Range" in responses["416"]["headers"]
