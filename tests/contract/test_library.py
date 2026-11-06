import pytest
from pydantic import ValidationError

from voice_archive_api.services.library.schemas import AskRequest

from .helpers import assert_operation_contract, assert_request_body_contract


def test_book_answer_contract(openapi_document):
    assert_operation_contract(
        openapi_document,
        "/api/v1/library/ask",
        "post",
        "ask_api_v1_library",
        {"200": "AskResult", "404": "BookErrorResponse", "422": "ValidationErrorResponse"},
    )
    assert_request_body_contract(openapi_document, "/api/v1/library/ask", "post", "AskRequest")


@pytest.mark.parametrize(
    "fields",
    [
        {"query": " "},
        {"book_id": 0},
        {"book_id": True},
        {"book_id": "1"},
        {"query": "x" * 8001},
        {"use_whole_book": True},
        {"effort": "extreme"},
    ],
)
def test_answer_request_rejects_invalid_fields(fields):
    values = {"book_id": 1, "query": "What happened?", **fields}

    with pytest.raises(ValidationError):
        AskRequest.model_validate(values)
