import logging
from traceback import extract_tb
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from voice_archive_api.application.content_validation.validation import ContentValidationError
from voice_archive_api.core.errors import ApplicationError

logger = logging.getLogger(__name__)

APPLICATION_ERROR_STATUSES = {
    "book_not_found": 404,
    "duplicate_book": 409,
    "file_not_found": 404,
    "file_already_linked": 409,
    "file_kind_mismatch": 422,
    "invalid_image": 422,
    "unsupported_media_type": 415,
    "invalid_pdf": 422,
    "encrypted_pdf_not_supported": 422,
    "file_too_large": 413,
    "invalid_multipart": 400,
    "invalid_audio": 422,
    "content_purge_required": 409,
    "content_exists": 409,
    "content_precondition_required": 428,
    "content_precondition_failed": 412,
}


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


COMMON_ERROR_RESPONSES = {
    500: {"model": InternalErrorResponse, "description": "Unexpected service error"},
    405: {"model": HttpErrorResponse, "description": "Method not allowed"},
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ContentValidationError)
    async def invalid_content(request: Request, exc: ContentValidationError) -> JSONResponse:
        from dataclasses import asdict

        oversized = any(issue.code == "payload_limit" for issue in exc.issues)
        return JSONResponse(
            status_code=413 if oversized else 422,
            content={
                "error": {
                    "code": "content_payload_too_large" if oversized else "content_validation_error",
                    "message": "Content validation failed",
                },
                "issues": [asdict(issue) for issue in exc.issues],
            },
        )

    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, exc: ApplicationError) -> JSONResponse:
        status = APPLICATION_ERROR_STATUSES.get(getattr(exc, "code", ""))
        if status is None:
            return await unexpected_error(request, exc)
        return JSONResponse(
            status_code=status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = {404: "route_not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": str(exc.detail)}},
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Stack locations and exception type are useful without recording exception
        # text/locals, which can contain SQL parameters or submitted file content.
        locations = "\n".join(
            f"{frame.filename}:{frame.lineno} in {frame.name}" for frame in extract_tb(exc.__traceback__)
        )
        logger.error("Unhandled request failure (%s)\n%s", type(exc).__name__, locations)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "An unexpected error occurred"}},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                },
                "issues": [
                    {
                        "location": list(issue["loc"]),
                        "code": issue["type"],
                        "message": issue["msg"],
                    }
                    for issue in exc.errors()
                ],
            },
        )
