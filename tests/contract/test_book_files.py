import pytest

from .helpers import assert_binary_response_contract, assert_multipart_file_body_contract, assert_operation_contract


@pytest.mark.parametrize(
    "path,method,operation_id,responses",
    [
        (
            "/api/v1/files/{kind}",
            "post",
            "create_api_v1_file",
            {
                "201": "FileResponse",
                "400": "FileUploadErrorResponse",
                "413": "FileUploadErrorResponse",
                "415": "FileErrorResponse",
                "422": ["FileErrorResponse", "ValidationErrorResponse"],
            },
        ),
        ("/api/v1/files/{file_id}", "get", "get_api_v1_file", {"404": "FileErrorResponse", "416": None}),
        ("/api/v1/files/{file_id}", "head", "head_api_v1_file", {"200": None, "206": None, "404": "FileErrorResponse"}),
        (
            "/api/v1/files/{file_id}",
            "delete",
            "delete_api_v1_file",
            {"204": None, "404": "FileErrorResponse", "409": "FileErrorResponse"},
        ),
    ],
)
def test_file_operation_contracts(openapi_document, path, method, operation_id, responses):
    assert_operation_contract(openapi_document, path, method, operation_id, responses)


def test_upload_body_contract_requires_a_multipart_file(openapi_document):
    assert_multipart_file_body_contract(openapi_document, "/api/v1/files/{kind}", "post", "file")


def test_upload_response_contract_exposes_only_public_file_details(openapi_document):
    schemas = openapi_document["components"]["schemas"]
    schema = schemas["FileResponse"]
    variants = {"cover": "CoverFileResponse", "source_pdf": "SourcePdfFileResponse"}
    assert schema["discriminator"] == {
        "propertyName": "kind",
        "mapping": {kind: f"#/components/schemas/{model}" for kind, model in variants.items()},
    }
    assert schema["oneOf"] == [{"$ref": f"#/components/schemas/{model}"} for model in variants.values()]
    fields = {"id", "kind", "byte_size", "metadata", "created_at"}
    for kind, model in variants.items():
        assert set(schemas[model]["properties"]) == fields
        assert set(schemas[model]["required"]) == fields
        assert schemas[model]["properties"]["kind"]["const"] == kind


@pytest.mark.parametrize(
    "response_model,metadata_model,fields",
    [
        ("CoverFileResponse", "CoverMetadata", {"width", "height"}),
        ("SourcePdfFileResponse", "SourcePdfMetadata", {"page_count"}),
    ],
)
def test_upload_metadata_contract_requires_positive_integers(openapi_document, response_model, metadata_model, fields):
    schemas = openapi_document["components"]["schemas"]
    assert schemas[response_model]["properties"]["metadata"] == {"$ref": f"#/components/schemas/{metadata_model}"}
    metadata = schemas[metadata_model]
    assert set(metadata["required"]) == set(metadata["properties"]) == fields
    for field in fields:
        assert metadata["properties"][field]["type"] == "integer"
        assert metadata["properties"][field]["exclusiveMinimum"] == 0
        assert metadata["properties"][field]["maximum"] == 2_147_483_647


@pytest.mark.parametrize("path", ["/api/v1/files/cover", "/api/v1/files/source_pdf"])
@pytest.mark.parametrize(
    "case", ["missing", "wrong-name", "multiple", "text-field", "missing-boundary", "malformed", "not-multipart"]
)
def test_invalid_upload_request_has_a_documented_error(client, file_storage, path, case):
    if case == "missing":
        response = client.post(
            path,
            content=b"--empty--\r\n",
            headers={
                "Content-Type": "multipart/form-data; boundary=empty",
            },
        )
    elif case == "wrong-name":
        response = client.post(path, files={"other": ("file.bin", b"data")})
    elif case == "multiple":
        response = client.post(path, files=[("file", ("a", b"a")), ("file", ("b", b"b"))])
    elif case == "text-field":
        response = client.post(path, files={"file": ("a", b"a")}, data={"book_id": "1"})
    elif case == "missing-boundary":
        response = client.post(path, content=b"data", headers={"Content-Type": "multipart/form-data"})
    elif case == "malformed":
        response = client.post(
            path,
            content=b"not the boundary",
            headers={
                "Content-Type": "multipart/form-data; boundary=expected",
            },
        )
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


@pytest.mark.parametrize(
    "path,media_type",
    [
        ("/api/v1/files/{file_id}", "image/webp"),
        ("/api/v1/files/{file_id}", "application/pdf"),
        ("/api/v1/files/{file_id}", "audio/mpeg"),
    ],
)
@pytest.mark.parametrize("status", ["200", "206"])
def test_file_delivery_binary_contracts(openapi_document, path, media_type, status):
    assert_binary_response_contract(
        openapi_document,
        path,
        "get",
        status,
        media_type,
        additional_media_types=tuple({"image/webp", "application/pdf", "audio/mpeg"} - {media_type})
        + (("multipart/byteranges",) if status == "206" else ()),
    )
    assert "requestBody" not in openapi_document["paths"][path]["get"]


@pytest.mark.parametrize("path", ["/api/v1/files/cover", "/api/v1/files/source_pdf"])
@pytest.mark.parametrize("method", ["get", "head", "delete"])
@pytest.mark.parametrize("file_id", ["0", "invalid"])
def test_invalid_delivery_id_has_a_documented_error(client, path, method, file_id):
    response = client.request(method, f"/api/v1/files/{file_id}")
    assert response.status_code == 422
    if method != "head":
        assert response.json()["error"]["code"] == "validation_error"
    else:
        assert response.content == b""


@pytest.mark.parametrize("path", ["/api/v1/files/{file_id}", "/api/v1/files/{file_id}"])
def test_delivery_range_errors_document_native_text_response(openapi_document, path):
    responses = openapi_document["paths"][path]["get"]["responses"]
    assert responses["400"]["content"] == {"text/plain": {"schema": {"type": "string"}}}
    assert "content" not in responses["416"]
    assert "Content-Range" in responses["416"]["headers"]
