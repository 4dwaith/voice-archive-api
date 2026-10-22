import json

import pytest
from pydantic import ValidationError

from voice_archive_api.services.books.schemas import BookCreate, BookPatch

from .helpers import assert_operation_contract, assert_request_body_contract


@pytest.mark.parametrize("model", ["BookCreate", "BookPatch", "BookResponse"])
@pytest.mark.parametrize("field,limit", [("title", 750), ("author", 450), ("description", 15_000)])
def test_openapi_preserves_unicode_length_constraints(openapi_document, model, field, limit):
    prop = openapi_document["components"]["schemas"][model]["properties"][field]
    text = next(branch for branch in prop.get("anyOf", [prop]) if branch.get("type") == "string")
    assert text["maxLength"] == limit


@pytest.mark.parametrize(
    "path,method,operation_id,responses",
    [
        (
            "/api/v1/books",
            "post",
            "create_api_v1_book",
            {
                "201": "BookResponse",
                "404": "FileErrorResponse",
                "409": "BookErrorResponse",
                "422": ["ValidationErrorResponse", "FileErrorResponse"],
            },
        ),
        (
            "/api/v1/books",
            "get",
            "list_api_v1_books",
            {"200": "BookList", "422": "ValidationErrorResponse"},
        ),
        (
            "/api/v1/books/{book_id}",
            "get",
            "get_api_v1_book",
            {"200": "BookResponse", "404": "BookErrorResponse", "422": "ValidationErrorResponse"},
        ),
        (
            "/api/v1/books/{book_id}",
            "patch",
            "patch_api_v1_book",
            {
                "200": "BookResponse",
                "404": ["BookErrorResponse", "FileErrorResponse"],
                "409": "BookErrorResponse",
                "422": ["ValidationErrorResponse", "FileErrorResponse"],
            },
        ),
        (
            "/api/v1/books/{book_id}",
            "delete",
            "delete_api_v1_book",
            {"204": None, "404": "BookErrorResponse", "422": "ValidationErrorResponse"},
        ),
    ],
    ids=["create", "list", "retrieve", "update", "delete"],
)
def test_book_operation_contracts(openapi_document, path, method, operation_id, responses):
    assert_operation_contract(openapi_document, path, method, operation_id, responses)


@pytest.mark.parametrize(
    "path,method,request_model",
    [
        ("/api/v1/books", "post", "BookCreate"),
        ("/api/v1/books/{book_id}", "patch", "BookPatch"),
    ],
    ids=["create", "update"],
)
def test_book_request_body_contracts(openapi_document, path, method, request_model):
    assert_request_body_contract(openapi_document, path, method, request_model)


def test_book_contract_documents_nullable_delivery_urls(openapi_document):
    schema = openapi_document["components"]["schemas"]["BookResponse"]
    for field in ("cover_url", "source_pdf_url"):
        assert field in schema["required"]
        assert schema["properties"][field]["readOnly"] is True
        assert {item["type"] for item in schema["properties"][field]["anyOf"]} == {"string", "null"}


@pytest.mark.parametrize("field", ["title", "language"])
def test_patch_schema_keeps_non_nullable_fields_omittable(field):
    schema = BookPatch.model_json_schema()
    assert field not in schema.get("required", [])
    assert schema["properties"][field]["type"] == "string"
    assert "default" not in schema["properties"][field]


def test_patch_rejects_uneditable_fields():
    with pytest.raises(ValidationError):
        BookPatch(id=1)


@pytest.mark.parametrize("field", ["cover_file_id", "source_pdf_file_id"])
@pytest.mark.parametrize("model", [BookCreate, BookPatch])
@pytest.mark.parametrize("value", [0, -1, 9_223_372_036_854_775_808, True, 1.5, "1"])
def test_file_link_ids_reject_invalid_values(model, field, value):
    base = {"title": "Book", "language": "en"} if model is BookCreate else {}
    with pytest.raises(ValidationError):
        model(**base, **{field: value})


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"title": 123, "language": "en"}, "title"),
        ({"title": "Book", "language": "en", "id": 1}, "id"),
        ({"title": "a\ud800b", "language": "en"}, "title"),
    ],
    ids=["numeric-title", "extra-id", "unpaired-surrogate"],
)
def test_invalid_create_wire_values_do_not_create_books(client, payload, field):
    # ensure_ascii=True carries invalid Unicode as a JSON escape, allowing the
    # server to reject it rather than failing in the client's UTF-8 encoder.
    response = client.post(
        "/api/v1/books",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert any(issue["location"] == ["body", field] for issue in body["issues"])
    assert client.get("/api/v1/books").json()["total"] == 0


@pytest.mark.parametrize(
    "method,book_id",
    [
        ("GET", "0"),
        ("GET", "-1"),
        ("GET", "abc"),
        ("PATCH", "1.5"),
        ("DELETE", "abc"),
        ("GET", "9223372036854775808"),
    ],
)
def test_invalid_book_ids_have_validation_errors(client, method, book_id):
    kwargs = {"json": {}} if method == "PATCH" else {}
    response = client.request(method, f"/api/v1/books/{book_id}", **kwargs)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert any(issue["location"] == ["path", "book_id"] for issue in body["issues"])


@pytest.mark.parametrize("field,value", [("limit", "abc"), ("offset", "1.5")])
def test_pagination_requires_integer_query_values(client, field, value):
    response = client.get("/api/v1/books", params={field: value})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert any(issue["location"] == ["query", field] for issue in body["issues"])
