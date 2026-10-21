"""Request bounds and errors for file uploads."""

from collections.abc import Callable, Coroutine
from typing import Literal

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from python_multipart.exceptions import MultipartParseError
from starlette.exceptions import HTTPException
from starlette.types import Message

from voice_archive_api.api.errors import ErrorResponse
from voice_archive_api.book_files.model import FileKind
from voice_archive_api.book_files.service import FILE_STRATEGIES
from voice_archive_api.core.dependencies import get_app_settings
from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.file_handling import FileTooLarge

MULTIPART_OVERHEAD_BYTES = 64 * 1024


class InvalidMultipart(ApplicationError):
    """An upload must contain exactly one file part and no extra form fields."""

    code = "invalid_multipart"
    message = "Expected a multipart upload with one file field named 'file'"


class FileUploadErrorResponse(ErrorResponse[Literal["file_too_large", "invalid_multipart"]]):
    """Errors shared by cover and PDF upload requests."""


class UploadRoute(APIRoute):
    """Bound multipart spooling before FastAPI validates the uploaded file field."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[None, None, Response]]:
        handler = super().get_route_handler()
        if self.methods is None or "POST" not in self.methods:
            return handler

        async def bounded_upload(request: Request) -> Response:
            settings = get_app_settings(request)
            raw_kind = request.path_params["kind"]
            try:
                kind = FileKind(raw_kind)
            except ValueError as exc:
                raise RequestValidationError(
                    [
                        {
                            "type": "enum",
                            "loc": ("path", "kind"),
                            "input": raw_kind,
                            "msg": "Unsupported file kind",
                            "ctx": {"expected": ", ".join(FileKind)},
                        }
                    ]
                ) from exc
            strategy = FILE_STRATEGIES[kind]
            file_size_limit = getattr(settings, strategy.limit_setting_key) + MULTIPART_OVERHEAD_BYTES

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
