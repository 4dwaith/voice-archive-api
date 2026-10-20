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
from voice_archive_api.book_files.pdfs import EncryptedPdfNotSupported, InvalidPdf
from voice_archive_api.book_files.schemas import SourcePdfFileResponse
from voice_archive_api.core.dependencies import AppSettings
from voice_archive_api.db.session import get_db


type SourcePdfFileErrorCode = Literal[
    "source_pdf_file_not_found", "invalid_pdf", "encrypted_pdf_not_supported",
]


class SourcePdfFileErrorResponse(ErrorResponse[SourcePdfFileErrorCode]):
    """Errors owned by source-PDF operations, also used when linking a PDF."""


def install_pdf_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(service.SourcePdfFileNotFound)
    async def missing_pdf(request: Request, exc: service.SourcePdfFileNotFound):
        return JSONResponse(status_code=404, content={"error": {
            "code": "source_pdf_file_not_found", "message": "Source PDF file not found",
        }})

    @app.exception_handler(InvalidPdf)
    async def invalid_pdf(request: Request, exc: InvalidPdf):
        return JSONResponse(status_code=422, content={"error": {
            "code": "invalid_pdf", "message": "Source file must be a readable PDF with at least one page",
        }})

    @app.exception_handler(EncryptedPdfNotSupported)
    async def encrypted_pdf(request: Request, exc: EncryptedPdfNotSupported):
        return JSONResponse(status_code=422, content={"error": {
            "code": "encrypted_pdf_not_supported", "message": "Encrypted PDFs are not supported",
        }})


class PdfUploadRoute(UploadRoute):
    limit_setting_key = "pdf_max_bytes"


router = APIRouter(prefix="/source-pdf-files", tags=["Source PDF files"], route_class=PdfUploadRoute)


@router.post(
    "", status_code=201, response_model=SourcePdfFileResponse,
    operation_id="create_api_v1_source_pdf_file",
    responses={
        400: {"model": FileUploadErrorResponse, "description": "Invalid multipart upload"},
        413: {"model": FileUploadErrorResponse, "description": "Upload exceeds the configured size limit"},
        422: {"model": SourcePdfFileErrorResponse | ValidationErrorResponse, "description": "Invalid/encrypted PDF or invalid request"},
    },
)
def create_source_pdf_file(
    file: Annotated[UploadFile, File(description="Unencrypted PDF with at least one page; original bytes are preserved")],
    db: Annotated[Session, Depends(get_db)], storage: UploadStorage, settings: AppSettings,
):
    """Upload and prepare a PDF. Link its returned ID through an explicit book create/save."""
    return service.create_pdf_file(db, file.file, storage, max_bytes=settings.pdf_max_bytes)
