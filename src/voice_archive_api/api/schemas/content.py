from typing import Literal

from pydantic import BaseModel

from voice_archive_api.api.schemas.errors import ErrorResponse


class ContentIssueResponse(BaseModel):
    code: str
    path: str
    source_key: str | None
    detail: str


class ContentValidationResponse(ErrorResponse[Literal["content_validation_error"]]):
    issues: list[ContentIssueResponse]


class ContentErrorResponse(
    ErrorResponse[
        Literal[
            "content_exists",
            "content_precondition_required",
            "content_precondition_failed",
            "content_payload_too_large",
            "content_version_mismatch",
        ]
    ]
):
    pass
