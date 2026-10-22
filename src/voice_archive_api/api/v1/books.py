from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.orm import Session

from voice_archive_api.api.errors import ErrorResponse, ValidationErrorResponse
from voice_archive_api.api.v1.files import FileErrorResponse
from voice_archive_api.db.session import get_db
from voice_archive_api.domain.books import service
from voice_archive_api.domain.books.model import Book
from voice_archive_api.domain.books.schemas import (
    BookCreate,
    BookList,
    BookPatch,
    BookResponse,
)

type BookErrorCode = Literal[
    "book_not_found",
    "duplicate_book",
    "file_already_linked",
    "content_purge_required",
]


class BookErrorResponse(ErrorResponse[BookErrorCode]):
    """HTTP error envelope for book operations."""


router = APIRouter(
    prefix="/books",
    tags=["Books"],
    responses={
        422: {"model": ValidationErrorResponse, "description": "Invalid request"},
    },
)
Database = Annotated[Session, Depends(get_db)]
BookId = Annotated[int, Path(gt=0, le=9_223_372_036_854_775_807)]

# OpenAPI responses
missing_book: dict[int | str, dict[str, Any]] = {404: {"model": BookErrorResponse, "description": "Book not found"}}
missing_file: dict[int | str, dict[str, Any]] = {
    404: {
        "model": FileErrorResponse,
        "description": "Referenced prepared file not found",
    }
}
missing_book_or_file: dict[int | str, dict[str, Any]] = {
    404: {
        "model": BookErrorResponse | FileErrorResponse,
        "description": "Book or referenced prepared file not found",
    }
}
invalid_file_link: dict[int | str, dict[str, Any]] = {
    422: {"model": ValidationErrorResponse | FileErrorResponse, "description": "Invalid request or file kind"}
}
conflict: dict[int | str, dict[str, Any]] = {
    409: {
        "model": BookErrorResponse,
        "description": "Duplicate title and author, or a cover/PDF already linked to another book",
    }
}


@router.post(
    "",
    status_code=201,
    response_model=BookResponse,
    operation_id="create_api_v1_book",
    responses=missing_file | conflict | invalid_file_link,
)
def create_book(payload: BookCreate, db: Database) -> Book:
    """Create a book. Title and language are required; prepared cover/PDF IDs are optional."""
    return service.create_book(db, payload)


@router.get("", response_model=BookList, operation_id="list_api_v1_books")
def list_books(
    db: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=9_223_372_036_854_775_807)] = 0,
) -> BookList:
    """List books in ascending ID order, with a bounded page and total count."""
    items, total = service.list_books(db, limit=limit, offset=offset)
    return BookList(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/{book_id}",
    response_model=BookResponse,
    operation_id="get_api_v1_book",
    responses=missing_book,
)
def get_book(book_id: BookId, db: Database) -> Book:
    return service.get_book(db, book_id)


@router.patch(
    "/{book_id}",
    response_model=BookResponse,
    operation_id="patch_api_v1_book",
    responses=missing_book_or_file | conflict | invalid_file_link,
)
def patch_book(book_id: BookId, payload: BookPatch, db: Database) -> Book:
    """Update supplied fields. Omission preserves values; only nullable fields accept null.

    Optional cover/PDF IDs attach prepared files. PDF links may be changed if
    a book has no content; afterward, changing the PDF requires content purge.
    """
    return service.patch_book(db, book_id, payload)


@router.delete(
    "/{book_id}",
    status_code=204,
    operation_id="delete_api_v1_book",
    responses=missing_book,
    response_class=Response,
)
def delete_book(book_id: BookId, db: Database) -> Response:
    if not service.delete_book(db, book_id):
        raise service.BookNotFound
    return Response(status_code=204)
