"""Shared request bounds and errors for the two file upload APIs."""

from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from python_multipart.exceptions import MultipartParseError
from starlette.exceptions import HTTPException
from starlette.types import Message

from voice_archive_api.api.errors import ErrorResponse
from voice_archive_api.core.dependencies import get_app_settings
from voice_archive_api.file_handling import FileTooLarge

MULTIPART_OVERHEAD_BYTES = 64 * 1024


class InvalidMultipart(Exception):
    """An upload must contain exactly one file part and no extra form fields."""


class FileUploadErrorResponse(ErrorResponse[Literal["file_too_large", "invalid_multipart"]]):
    """Errors shared by cover and PDF upload requests."""


def install_upload_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(FileTooLarge)
    async def too_large(request: Request, exc: FileTooLarge):
        return JSONResponse(status_code=413, content={"error": {
            "code": "file_too_large", "message": "Upload exceeds the configured size limit",
        }})

    @app.exception_handler(InvalidMultipart)
    async def invalid_multipart(request: Request, exc: InvalidMultipart):
        return JSONResponse(status_code=400, content={"error": {
            "code": "invalid_multipart", "message": "Expected a multipart upload with one file field named 'file'",
        }})


class UploadRoute(APIRoute):
    """Bound multipart spooling before FastAPI validates the uploaded file field."""

    limit_setting_key: str

    def get_route_handler(self):
        handler = super().get_route_handler()
        if "POST" not in self.methods:
            return handler

        async def bounded_upload(request: Request):
            settings = get_app_settings(request)
            file_size_limit = getattr(settings, self.limit_setting_key) + MULTIPART_OVERHEAD_BYTES

            if settings.upload_max_bytes is not None:
                # There's an overall limit on max bytes for all file uploads.
                file_size_limit = min(file_size_limit, settings.upload_max_bytes)

            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
                raise InvalidMultipart

            # Tracking the no of bytes received bounds uploads without Content-Length.
            bytes_received = 0

            async def receive_with_size_limit() -> Message:
                # Enforces uploaded file size.
                nonlocal bytes_received
                message = await request.receive()
                if message["type"] == "http.request":
                    bytes_received += len(message.get("body", b""))
                    if bytes_received > file_size_limit:
                        raise FileTooLarge
                return message

            limited_request = Request(request.scope, receive=receive_with_size_limit)
            try:
                # Large parts spool to disk. Parsing errors close partial spools;
                # this context closes completed ones, including on service failure.
                async with limited_request.form(max_files=1, max_fields=0):
                    return await handler(limited_request)
            except HTTPException as exc:
                if exc.status_code == 400:
                    raise InvalidMultipart from exc
                raise
            except MultipartParseError as exc:
                raise InvalidMultipart from exc

        return bounded_upload
