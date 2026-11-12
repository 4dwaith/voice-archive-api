from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Response

from voice_archive_api.api.schemas.books import BookErrorResponse
from voice_archive_api.api.schemas.errors import ValidationErrorResponse
from voice_archive_api.api.schemas.series import SeriesErrorResponse
from voice_archive_api.api.v1.books import Database
from voice_archive_api.db.models.series import Series
from voice_archive_api.services.books.schemas import FileId
from voice_archive_api.services.series import service
from voice_archive_api.services.series.schemas import (
    SeriesCreate,
    SeriesDetail,
    SeriesList,
    SeriesPatch,
    SeriesResponse,
)

router = APIRouter(
    prefix="/series",
    tags=["Series"],
    responses={422: {"model": ValidationErrorResponse, "description": "Invalid request"}},
)
SeriesId = Annotated[int, Path(gt=0, le=9_223_372_036_854_775_807)]
missing_series: dict[int | str, dict[str, Any]] = {
    404: {"model": SeriesErrorResponse, "description": "Series not found"}
}


@router.post("", status_code=201, response_model=SeriesResponse, operation_id="create_api_v1_series")
def create_series(payload: SeriesCreate, db: Database) -> Series:
    return service.create_series(db, payload)


@router.get("", response_model=SeriesList, operation_id="list_api_v1_series")
def list_series(
    db: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=9_223_372_036_854_775_807)] = 0,
) -> SeriesList:
    items, total = service.list_series(db, limit=limit, offset=offset)
    return SeriesList(items=items, total=total, limit=limit, offset=offset)


@router.get("/{series_id}", response_model=SeriesDetail, operation_id="get_api_v1_series", responses=missing_series)
def get_series(series_id: SeriesId, db: Database) -> SeriesDetail:
    series = service.get_series(db, series_id)
    book_ids = service.list_member_book_ids(db, series_id)
    return SeriesDetail(**SeriesResponse.model_validate(series).model_dump(), book_ids=book_ids)


@router.patch(
    "/{series_id}", response_model=SeriesResponse, operation_id="patch_api_v1_series", responses=missing_series
)
def patch_series(series_id: SeriesId, payload: SeriesPatch, db: Database) -> Series:
    return service.patch_series(db, series_id, payload)


@router.delete(
    "/{series_id}",
    status_code=204,
    operation_id="delete_api_v1_series",
    responses=missing_series,
    response_class=Response,
)
def delete_series(series_id: SeriesId, db: Database) -> Response:
    service.delete_series(db, series_id)
    return Response(status_code=204)


@router.put(
    "/{series_id}/books",
    response_model=SeriesDetail,
    operation_id="replace_api_v1_series_books",
    responses={
        404: {"model": SeriesErrorResponse | BookErrorResponse, "description": "Series or referenced book not found"},
        409: {"model": SeriesErrorResponse, "description": "Invalid membership or order"},
    },
)
def replace_series_books(series_id: SeriesId, book_ids: list[FileId], db: Database) -> SeriesDetail:
    series = service.replace_series_books(db, series_id, book_ids)
    return SeriesDetail(**SeriesResponse.model_validate(series).model_dump(), book_ids=book_ids)
