from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Path, Response, UploadFile
from sqlalchemy.orm import Session

from voice_archive_api.api.schemas.errors import ValidationErrorResponse
from voice_archive_api.api.schemas.files import FileErrorResponse, FileUploadErrorResponse
from voice_archive_api.api.v1.file_uploads import UploadRoute
from voice_archive_api.core.dependencies import AppSettings, Delivery, FileStorage
from voice_archive_api.db.models.file import StoredFile
from voice_archive_api.db.session import get_db
from voice_archive_api.file_handling.file_delivery import delivery_parameters, delivery_responses
from voice_archive_api.logic.files import FileKind
from voice_archive_api.services.files import service
from voice_archive_api.services.files.schemas import FileResponse
from voice_archive_api.services.files.service import FILE_STRATEGIES

router = APIRouter(prefix="/files", tags=["Files"], route_class=UploadRoute)
FileId = Annotated[int, Path(gt=0, le=9_223_372_036_854_775_807)]
invalid_request: dict[int | str, dict[str, Any]] = {
    422: {"model": ValidationErrorResponse, "description": "Invalid request"}
}
missing_file: dict[int | str, dict[str, Any]] = {
    404: {"model": FileErrorResponse, "description": "File record or stored bytes not found"}
}


def file_delivery_responses(*, head: bool = False) -> dict[int | str, dict[str, Any]]:
    responses = delivery_responses("application/octet-stream", head=head)
    if not head:
        for status in (200, 206):
            content = responses[status]["content"]
            binary = content.pop("application/octet-stream")
            content.update({strategy.media_type: binary for strategy in FILE_STRATEGIES.values()})
    return responses


@router.post(
    "/{kind}",
    status_code=201,
    response_model=FileResponse,
    operation_id="create_api_v1_file",
    responses={
        400: {"model": FileUploadErrorResponse, "description": "Invalid multipart upload"},
        413: {"model": FileUploadErrorResponse, "description": "Upload exceeds the configured size limit"},
        415: {"model": FileErrorResponse, "description": "Unsupported format"},
        422: {"model": FileErrorResponse | ValidationErrorResponse, "description": "Invalid file, kind, or request"},
    },
)
def create_file(
    kind: FileKind,
    file: Annotated[UploadFile, File(description="File bytes, validated and prepared according to kind")],
    db: Annotated[Session, Depends(get_db)],
    storage: FileStorage,
    settings: AppSettings,
) -> StoredFile:
    """Prepare a file independently of book create/save; return its ID for linking."""
    strategy = FILE_STRATEGIES[kind]
    assert strategy.limit_setting_key is not None
    return service.create_file(db, kind, file.file, storage, max_bytes=getattr(settings, strategy.limit_setting_key))


@router.get(
    "/{file_id}",
    response_class=Response,
    operation_id="get_api_v1_file",
    responses=file_delivery_responses() | missing_file | invalid_request,
    openapi_extra=delivery_parameters,
)
def get_file(
    file_id: FileId,
    db: Annotated[Session, Depends(get_db, scope="function")],
    delivery: Delivery,
) -> Response:
    """Deliver prepared bytes with native Range, If-Range, and conditional headers."""
    record = service.get_file(db, file_id)
    strategy = FILE_STRATEGIES[record.kind]
    try:
        return delivery.response(record.storage_key, media_type=strategy.media_type, filename=strategy.filename)
    except FileNotFoundError as exc:
        raise service.FileNotFound from exc


@router.head(
    "/{file_id}",
    response_class=Response,
    operation_id="head_api_v1_file",
    responses=file_delivery_responses(head=True) | missing_file | invalid_request,
    openapi_extra=delivery_parameters,
)
def head_file(
    file_id: FileId,
    db: Annotated[Session, Depends(get_db, scope="function")],
    delivery: Delivery,
) -> Response:
    return get_file(file_id, db, delivery)


@router.delete(
    "/{file_id}",
    status_code=204,
    response_class=Response,
    operation_id="delete_api_v1_file",
    responses=invalid_request
    | missing_file
    | {
        409: {"model": FileErrorResponse, "description": "File is linked to an owner"},
    },
)
def delete_file(file_id: FileId, db: Annotated[Session, Depends(get_db)]) -> Response:
    service.delete_file(db, file_id)
    return Response(status_code=204)
