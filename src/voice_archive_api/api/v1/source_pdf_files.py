from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, File, Path, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from voice_archive_api.api.errors import ErrorResponse, ValidationErrorResponse
from voice_archive_api.file_handling.file_delivery import delivery_parameters, delivery_responses
from voice_archive_api.api.v1.file_uploads import (
    FileUploadErrorResponse,
    UploadRoute,
)
from voice_archive_api.book_files import service
from voice_archive_api.book_files.pdfs import EncryptedPdfNotSupported, InvalidPdf
from voice_archive_api.book_files.schemas import SourcePdfFileResponse
from voice_archive_api.core.dependencies import AppSettings, Delivery, FileStorage
from voice_archive_api.db.session import get_db


type SourcePdfFileErrorCode = Literal[
    "source_pdf_file_not_found", "source_pdf_file_already_linked", "invalid_pdf", "encrypted_pdf_not_supported",
]


class SourcePdfFileErrorResponse(ErrorResponse[SourcePdfFileErrorCode]):
    """Errors owned by source-PDF operations, also used when linking a PDF."""


def install_pdf_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(service.SourcePdfFileAlreadyLinked)
    async def linked_pdf(request: Request, exc: service.SourcePdfFileAlreadyLinked):
        return JSONResponse(status_code=409, content={"error": {
            "code": "source_pdf_file_already_linked", "message": "Source PDF file is already linked to a book",
        }})

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
    db: Annotated[Session, Depends(get_db)], storage: FileStorage, settings: AppSettings,
):
    """Upload and prepare a PDF. Link its returned ID through an explicit book create/save."""
    return service.create_pdf_file(db, file.file, storage, max_bytes=settings.pdf_max_bytes)


FileId = Annotated[int, Path(gt=0, le=9_223_372_036_854_775_807)]
missing_file = {404: {"model": SourcePdfFileErrorResponse, "description": "Prepared file or stored bytes not found"}}
invalid_request = {422: {"model": ValidationErrorResponse, "description": "Invalid request"}}


@router.get(
    "/{file_id}", response_class=Response,
    operation_id="get_api_v1_source_pdf_file",
    responses=delivery_responses("application/pdf") | missing_file | invalid_request,
    openapi_extra=delivery_parameters,
)
def get_source_pdf_file(
    file_id: FileId, db: Annotated[Session, Depends(get_db, scope="function")], delivery: Delivery,
):
    """Retrieve a prepared file, including before linking it to a book.

    Range, If-Range, and file headers follow Starlette's native FileResponse behavior.
    """
    record = service.get_pdf_file(db, file_id)
    try:
        return delivery.response(record.storage_key, media_type="application/pdf", filename="source.pdf")
    except FileNotFoundError as exc:
        raise service.SourcePdfFileNotFound from exc


@router.head(
    "/{file_id}", response_class=Response,
    operation_id="head_api_v1_source_pdf_file",
    responses=delivery_responses("application/pdf", head=True) | missing_file | invalid_request,
    openapi_extra=delivery_parameters,
)
def head_source_pdf_file(
    file_id: FileId, db: Annotated[Session, Depends(get_db, scope="function")], delivery: Delivery,
):
    """Return file headers without the body, including native range handling."""
    return get_source_pdf_file(file_id, db, delivery)


@router.delete(
    "/{file_id}", status_code=204, response_class=Response,
    operation_id="delete_api_v1_source_pdf_file",
    responses=invalid_request | {
        404: {"model": SourcePdfFileErrorResponse, "description": "Prepared file not found"},
        409: {"model": SourcePdfFileErrorResponse, "description": "File is linked to a book"},
    },
)
def delete_source_pdf_file(file_id: FileId, db: Annotated[Session, Depends(get_db)]):
    """Remove an unlinked PDF and queue its stored bytes for maintenance."""
    service.delete_pdf_file(db, file_id)
    return Response(status_code=204)
