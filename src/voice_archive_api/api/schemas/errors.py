from typing import Literal

from pydantic import BaseModel


class ErrorDetail[CodeT: str](BaseModel):
    code: CodeT
    message: str


class ErrorResponse[CodeT: str](BaseModel):
    error: ErrorDetail[CodeT]


class ValidationIssue(BaseModel):
    location: list[str | int]
    code: str
    message: str


class ValidationErrorResponse(ErrorResponse[Literal["validation_error"]]):
    issues: list[ValidationIssue]


class InternalErrorResponse(ErrorResponse[Literal["internal_error"]]):
    """Unexpected failures never expose exception details to clients."""


class HttpErrorResponse(ErrorResponse[Literal["route_not_found", "method_not_allowed", "http_error"]]):
    """Framework errors outside the domain-specific exception handlers."""
