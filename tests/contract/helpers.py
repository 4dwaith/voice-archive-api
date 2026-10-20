"""Shared assertions for endpoint declarations in OpenAPI documents."""

from typing import Any


def assert_binary_response_contract(document, path, method, status, media_type, *, additional_media_types=()):
    response = document["paths"][path][method]["responses"][status]
    assert response["content"] == {
        name: {"schema": {"type": "string", "format": "binary"}}
        for name in (media_type, *additional_media_types)
    }
    assert {"Content-Length", "Content-Disposition", "Accept-Ranges", "ETag", "Last-Modified"} <= response["headers"].keys()
    if status == "206":
        assert "Content-Range" in response["headers"]


def assert_operation_contract(
    document: dict[str, Any],
    path: str,
    method: str,
    operation_id: str,
    responses: dict[str, str | list[str] | None],
) -> None:
    """
    Re-usable across APIs. Checks that the operations and responses on the OpenAPI document match the expected values.
    """
    operation = document["paths"][path][method]
    assert operation["operationId"] == operation_id, f"{method.upper()} {path}"
    for status, model in responses.items():
        response = operation["responses"][status]
        context = f"{method.upper()} {path}: response {status}"
        if model is None:
            assert "content" not in response, context
        elif isinstance(model, list):
            assert response["content"]["application/json"]["schema"]["anyOf"] == [
                {"$ref": f"#/components/schemas/{name}"} for name in model
            ], context
        else:
            assert response["content"]["application/json"]["schema"] == {
                "$ref": f"#/components/schemas/{model}"
            }, context


def assert_request_body_contract(
    document: dict[str, Any],
    path: str,
    method: str,
    request_model: str,
    *,
    required: bool = True,
) -> None:
    """
    Re-usable across APIs. Checks that the expected request body on the OpenAPI document matches the expected model model.
    """
    body = document["paths"][path][method]["requestBody"]
    context = f"{method.upper()} {path}: request body"
    assert body.get("required", False) is required, context
    assert body["content"]["application/json"]["schema"] == {
        "$ref": f"#/components/schemas/{request_model}"
    }, context


def assert_multipart_file_body_contract(
    document: dict[str, Any],
    path: str,
    method: str,
    file_field: str,
    *,
    required: bool = True,
) -> None:
    """Check a multipart request body containing one required binary file field."""
    body = document["paths"][path][method]["requestBody"]
    context = f"{method.upper()} {path}: multipart request body"
    assert body.get("required", False) is required, context
    assert set(body["content"]) == {"multipart/form-data"}, context
    reference = body["content"]["multipart/form-data"]["schema"]["$ref"].split("/")[-1]
    schema = document["components"]["schemas"][reference]
    assert schema["required"] == [file_field], context
    assert set(schema["properties"]) == {file_field}, context
    assert schema["properties"][file_field]["type"] == "string", context
    assert schema["properties"][file_field]["contentMediaType"] == "application/octet-stream", context
