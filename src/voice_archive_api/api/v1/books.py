from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from voice_archive_api.api.errors import ErrorResponse, ValidationErrorResponse
from voice_archive_api.books import service
from voice_archive_api.books.schemas import (
    BookCreate,
    BookList,
    BookPatch,
    BookResponse,
)
from voice_archive_api.db.session import get_db

type BookErrorCode = Literal["book_not_found", "duplicate_book"]


class BookErrorResponse(ErrorResponse[BookErrorCode]):
    """HTTP error envelope for book operations."""


def install_book_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(service.BookNotFound)
    async def missing_book(request: Request, exc: service.BookNotFound):
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "book_not_found", "message": "Book not found"}},
        )

    @app.exception_handler(service.DuplicateBook)
    async def duplicate_book(request: Request, exc: service.DuplicateBook):
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "duplicate_book",
                    "message": "A book with this title and author already exists",
                }
            },
        )


router = APIRouter(
    prefix="/books",
    tags=["Books"],
    responses={
        422: {"model": ValidationErrorResponse, "description": "Invalid request"},
    },
)
Database = Annotated[Session, Depends(get_db)]
BookId = Annotated[int, Path(gt=0, le=9_223_372_036_854_775_807)]
missing = {404: {"model": BookErrorResponse, "description": "Book not found"}}
conflict = {409: {"model": BookErrorResponse, "description": "Duplicate title and author"}}


@router.post(
    "",
    status_code=201,
    response_model=BookResponse,
    operation_id="create_api_v1_book",
    responses=conflict,
)
def create_book(payload: BookCreate, db: Database):
    """Create metadata. Title and language are required; files are added separately."""
    return service.create_book(db, payload)


@router.get("", response_model=BookList, operation_id="list_api_v1_books")
def list_books(
        db: Database,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=9_223_372_036_854_775_807)] = 0,
):
    """List books in ascending ID order, with a bounded page and total count."""
    items, total = service.list_books(db, limit=limit, offset=offset)
    return BookList(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/{book_id}",
    response_model=BookResponse,
    operation_id="get_api_v1_book",
    responses=missing,
)
def get_book(book_id: BookId, db: Database):
    return service.get_book(db, book_id)


@router.patch(
    "/{book_id}",
    response_model=BookResponse,
    operation_id="patch_api_v1_book",
    responses=missing | conflict,
)
def patch_book(book_id: BookId, payload: BookPatch, db: Database):
    """Update supplied fields. Omission preserves values; only nullable fields accept null."""
    return service.patch_book(db, book_id, payload)


@router.delete(
    "/{book_id}",
    status_code=204,
    operation_id="delete_api_v1_book",
    responses=missing,
    response_class=Response,
)
def delete_book(book_id: BookId, db: Database):
    if not service.delete_book(db, book_id):
        raise service.BookNotFound
    return Response(status_code=204)
