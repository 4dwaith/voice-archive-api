"""Shared assertions for endpoint declarations in OpenAPI documents."""

from typing import Any


def assert_operation_contract(
    document: dict[str, Any],
    path: str,
    method: str,
    operation_id: str,
    responses: dict[str, str | None],
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
