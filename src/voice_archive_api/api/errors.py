from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
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


def install_error_handlers(app: FastAPI) -> None:
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
