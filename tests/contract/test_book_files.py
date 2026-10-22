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


def test_upload_response_contract_discriminates_file_variants_by_kind(openapi_document):
    schema = openapi_document["components"]["schemas"]["FileResponse"]
    variants = {
        "cover": "CoverFileResponse",
        "source_pdf": "SourcePdfFileResponse",
        "audio_chapter": "AudioFileResponse",
    }
    assert schema["discriminator"] == {
        "propertyName": "kind",
        "mapping": {kind: f"#/components/schemas/{model}" for kind, model in variants.items()},
    }
    assert schema["oneOf"] == [{"$ref": f"#/components/schemas/{model}"} for model in variants.values()]


@pytest.mark.parametrize(
    "kind,model",
    [("cover", "CoverFileResponse"), ("source_pdf", "SourcePdfFileResponse"), ("audio_chapter", "AudioFileResponse")],
)
def test_upload_variant_contract_exposes_only_required_public_fields(openapi_document, kind, model):
    schema = openapi_document["components"]["schemas"][model]
    fields = {"id", "kind", "byte_size", "metadata", "created_at"}
    assert set(schema["properties"]) == fields
    assert set(schema["required"]) == fields
    assert schema["properties"]["kind"]["const"] == kind


@pytest.mark.parametrize(
    "response_model,metadata_model",
    [
        ("CoverFileResponse", "CoverMetadata"),
        ("SourcePdfFileResponse", "SourcePdfMetadata"),
        ("AudioFileResponse", "AudioMetadata"),
    ],
)
def test_upload_variant_contract_references_its_metadata_shape(openapi_document, response_model, metadata_model):
    schema = openapi_document["components"]["schemas"][response_model]
    assert schema["properties"]["metadata"] == {"$ref": f"#/components/schemas/{metadata_model}"}


@pytest.mark.parametrize(
    "metadata_model,fields",
    [
        ("CoverMetadata", {"width", "height"}),
        ("SourcePdfMetadata", {"page_count"}),
        ("AudioMetadata", {"duration_ms", "sample_rate", "channels"}),
    ],
)
def test_upload_metadata_contract_requires_all_declared_fields(openapi_document, metadata_model, fields):
    metadata = openapi_document["components"]["schemas"][metadata_model]
    assert set(metadata["required"]) == set(metadata["properties"]) == fields


@pytest.mark.parametrize(
    "metadata_model,field,maximum",
    [
        ("CoverMetadata", "width", 2_147_483_647),
        ("CoverMetadata", "height", 2_147_483_647),
        ("SourcePdfMetadata", "page_count", 2_147_483_647),
        ("AudioMetadata", "duration_ms", 9_223_372_036_854_775_807),
        ("AudioMetadata", "sample_rate", 2_147_483_647),
        ("AudioMetadata", "channels", 2_147_483_647),
    ],
)
def test_upload_metadata_contract_documents_positive_integer_bounds(openapi_document, metadata_model, field, maximum):
    schema = openapi_document["components"]["schemas"][metadata_model]["properties"][field]
    assert schema["type"] == "integer"
    assert schema["exclusiveMinimum"] == 0
    # Served OpenAPI represents large bounds as floats; response-validation tests
    # separately check the exact integer maximum and maximum + 1.
    assert schema["maximum"] == float(maximum)


@pytest.mark.parametrize("path", ["/api/v1/files/cover", "/api/v1/files/source_pdf", "/api/v1/files/audio_chapter"])
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


@pytest.mark.parametrize("media_type", ["image/webp", "application/pdf", "audio/mpeg"])
@pytest.mark.parametrize("status", ["200", "206"])
def test_file_delivery_binary_contracts(openapi_document, media_type, status):
    assert_binary_response_contract(
        openapi_document,
        "/api/v1/files/{file_id}",
        "get",
        status,
        media_type,
        additional_media_types=tuple(
            other for other in ("image/webp", "application/pdf", "audio/mpeg") if other != media_type
        )
        + (("multipart/byteranges",) if status == "206" else ()),
    )


def test_file_delivery_get_contract_has_no_request_body(openapi_document):
    assert "requestBody" not in openapi_document["paths"]["/api/v1/files/{file_id}"]["get"]


@pytest.mark.parametrize("method", ["get", "head", "delete"])
@pytest.mark.parametrize("file_id", ["0", "invalid"])
def test_invalid_delivery_id_has_a_documented_error(client, method, file_id):
    response = client.request(method, f"/api/v1/files/{file_id}")
    assert response.status_code == 422
    if method != "head":
        assert response.json()["error"]["code"] == "validation_error"
    else:
        assert response.content == b""


def test_invalid_range_contract_documents_native_text_response(openapi_document):
    responses = openapi_document["paths"]["/api/v1/files/{file_id}"]["get"]["responses"]
    assert responses["400"]["content"] == {"text/plain": {"schema": {"type": "string"}}}


def test_unsatisfiable_range_contract_exposes_content_range_header(openapi_document):
    responses = openapi_document["paths"]["/api/v1/files/{file_id}"]["get"]["responses"]
    assert "Content-Range" in responses["416"]["headers"]
