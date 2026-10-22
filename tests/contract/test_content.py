import pytest

from .helpers import assert_operation_contract, assert_request_body_contract


@pytest.mark.parametrize(
    "path,method,operation_id,responses",
    [
        (
            "/api/v1/books/{book_id}/content-imports",
            "post",
            "import_api_v1_book_content",
            {
                "200": "ImportResult",
                "404": "BookErrorResponse",
                "409": "ContentErrorResponse",
                "413": "ContentErrorResponse",
                "422": ["ContentValidationResponse", "ValidationErrorResponse"],
            },
        ),
        (
            "/api/v1/books/{book_id}/sections",
            "get",
            "list_api_v1_book_sections",
            {
                "200": "SectionPage",
                "404": "BookErrorResponse",
                "412": "ContentErrorResponse",
                "422": "ValidationErrorResponse",
            },
        ),
        (
            "/api/v1/books/{book_id}/segments",
            "get",
            "list_api_v1_book_segments",
            {
                "200": "SegmentPage",
                "404": "BookErrorResponse",
                "412": "ContentErrorResponse",
                "422": "ValidationErrorResponse",
            },
        ),
        (
            "/api/v1/books/{book_id}/content",
            "get",
            "get_api_v1_book_content",
            {"200": "ContentManifest", "404": "BookErrorResponse"},
        ),
        (
            "/api/v1/books/{book_id}/content",
            "delete",
            "purge_api_v1_book_content",
            {
                "204": None,
                "404": "BookErrorResponse",
                "412": "ContentErrorResponse",
                "428": "ContentErrorResponse",
            },
        ),
    ],
    ids=["import", "sections", "segments", "manifest", "purge"],
)
def test_content_operation_contracts(openapi_document, path, method, operation_id, responses):
    assert_operation_contract(openapi_document, path, method, operation_id, responses)


def test_content_import_request_body_contract(openapi_document):
    assert_request_body_contract(openapi_document, "/api/v1/books/{book_id}/content-imports", "post", "ContentInput")


def test_content_import_dry_run_defaults_to_false(openapi_document):
    operation = openapi_document["paths"]["/api/v1/books/{book_id}/content-imports"]["post"]
    query = next(p for p in operation["parameters"] if p["name"] == "dry_run")
    assert query["schema"]["default"] is False


def test_content_manifest_exposes_etag_header(openapi_document):
    operation = openapi_document["paths"]["/api/v1/books/{book_id}/content"]["get"]
    assert "ETag" in operation["responses"]["200"]["headers"]


@pytest.mark.parametrize("resource", ["sections", "segments"])
def test_content_collections_use_required_version_header_and_integer_offsets(openapi_document, resource):
    parameters = openapi_document["paths"][f"/api/v1/books/{{book_id}}/{resource}"]["get"]["parameters"]
    by_name = {parameter["name"]: parameter for parameter in parameters}
    header = by_name["if-content-version"]
    assert header["in"] == "header" and header["required"] is True
    assert header["schema"]["type"] == "string" and header["schema"]["format"] == "uuid"
    assert by_name["offset"]["in"] == "query" and by_name["offset"]["schema"]["default"] == 0
    assert "content_version" not in by_name and "cursor" not in by_name


def test_content_purge_if_match_header_is_a_nonnullable_string(openapi_document):
    operation = openapi_document["paths"]["/api/v1/books/{book_id}/content"]["delete"]
    header = next(p for p in operation["parameters"] if p["in"] == "header")
    assert header["name"] == "if-match" and header["schema"]["type"] == "string"
    assert "anyOf" not in header["schema"]
