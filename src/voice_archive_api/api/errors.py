from typing import Literal
import logging
from traceback import extract_tb

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)


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
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = {404: "route_not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": str(exc.detail)}},
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
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
    async def invalid_request(request: Request, exc: RequestValidationError):
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
