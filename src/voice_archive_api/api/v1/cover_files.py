from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from voice_archive_api.api.errors import ErrorResponse, ValidationErrorResponse
from voice_archive_api.api.v1.file_uploads import (
    FileUploadErrorResponse,
    UploadRoute,
    UploadStorage,
)
from voice_archive_api.book_files import service
from voice_archive_api.book_files.covers import InvalidImage, UnsupportedMediaType
from voice_archive_api.book_files.schemas import CoverFileResponse
from voice_archive_api.core.dependencies import AppSettings
from voice_archive_api.db.session import get_db


type CoverFileErrorCode = Literal[
    "cover_file_not_found", "invalid_image", "unsupported_media_type",
]


class CoverFileErrorResponse(ErrorResponse[CoverFileErrorCode]):
    """Errors owned by cover-file operations, also used when linking a cover."""


def install_cover_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(service.CoverFileNotFound)
    async def missing_cover(request: Request, exc: service.CoverFileNotFound):
        return JSONResponse(status_code=404, content={"error": {
            "code": "cover_file_not_found", "message": "Cover file not found",
        }})

    @app.exception_handler(InvalidImage)
    async def invalid_image(request: Request, exc: InvalidImage):
        return JSONResponse(status_code=422, content={"error": {
            "code": "invalid_image", "message": "Cover is empty, corrupt, or cannot be safely decoded and normalized",
        }})

    @app.exception_handler(UnsupportedMediaType)
    async def unsupported_image(request: Request, exc: UnsupportedMediaType):
        return JSONResponse(status_code=415, content={"error": {
            "code": "unsupported_media_type", "message": "Cover must be a JPEG, PNG, or WebP image",
        }})


class CoverUploadRoute(UploadRoute):
    limit_setting_key = "cover_max_bytes"


router = APIRouter(prefix="/cover-files", tags=["Cover files"], route_class=CoverUploadRoute)


@router.post(
    "", status_code=201, response_model=CoverFileResponse,
    operation_id="create_api_v1_cover_file",
    responses={
        400: {"model": FileUploadErrorResponse, "description": "Invalid multipart upload"},
        413: {"model": FileUploadErrorResponse, "description": "Upload exceeds the configured size limit"},
        415: {"model": CoverFileErrorResponse, "description": "Unsupported image format"},
        422: {"model": CoverFileErrorResponse | ValidationErrorResponse, "description": "Invalid image or request"},
    },
)
def create_cover_file(
    file: Annotated[UploadFile, File(description="JPEG, PNG, or WebP; normalized to a static WebP cover")],
    db: Annotated[Session, Depends(get_db)], storage: UploadStorage, settings: AppSettings,
):
    """Upload and prepare a cover. Link its returned ID through an explicit book create/save."""
    return service.create_cover_file(db, file.file, storage, max_bytes=settings.cover_max_bytes)
