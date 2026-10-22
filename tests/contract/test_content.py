from .helpers import assert_operation_contract


def test_content_openapi_contract(openapi_document):
    base = "/api/v1/books/{book_id}"
    assert_operation_contract(
        openapi_document,
        base + "/content-imports",
        "post",
        "import_api_v1_book_content",
        {
            "200": "ImportResult",
            "404": "BookErrorResponse",
            "409": "ContentErrorResponse",
            "413": "ContentErrorResponse",
            "422": ["ContentValidationResponse", "ValidationErrorResponse"],
        },
    )
    operation = openapi_document["paths"][base + "/content-imports"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ContentInput"
    }
    query = next(p for p in operation["parameters"] if p["name"] == "dry_run")
    assert query["schema"]["default"] is False
    get = openapi_document["paths"][base + "/content"]["get"]
    assert "ETag" in get["responses"]["200"]["headers"]
    delete = openapi_document["paths"][base + "/content"]["delete"]
    assert {"204", "404", "412", "428"} <= delete["responses"].keys()
    header = next(p for p in delete["parameters"] if p["in"] == "header")
    assert header["name"] == "if-match" and header["schema"]["type"] == "string"
    assert "anyOf" not in header["schema"]
