import pytest

from .helpers import assert_multipart_file_body_contract, assert_operation_contract


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
    ],
    ids=["create"],
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
    ],
    ids=["create"],
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
