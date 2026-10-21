from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from psycopg.errors import ForeignKeyViolation, UniqueViolation
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from voice_archive_api.book_files import service as file_service
from voice_archive_api.book_files.model import StoredFile
from voice_archive_api.books.constants import (
    FOREIGN_KEY_BOOK_COVER_FILE,
    FOREIGN_KEY_BOOK_SOURCE_PDF_FILE,
    UNIQUE_BOOK_COVER_FILE,
    UNIQUE_BOOK_IDENTITY,
    UNIQUE_BOOK_SOURCE_PDF_FILE,
)
from voice_archive_api.books.model import Book
from voice_archive_api.books.schemas import BookCreate, BookPatch
from voice_archive_api.books.validation import normalize_identity
from voice_archive_api.core.errors import ApplicationError


class BookNotFound(ApplicationError):
    code = "book_not_found"
    message = "Book not found"


class DuplicateBook(ApplicationError):
    code = "duplicate_book"
    message = "A book with this title and author already exists"


def get_book(db: Session, book_id: int, *, for_update: bool = False) -> Book:
    book = db.get(Book, book_id, with_for_update=for_update, populate_existing=for_update)
    if book is None:
        raise BookNotFound
    return book


def check_duplicate(db: Session, normalized_title: str, normalized_author: str, exclude_id: int | None = None) -> None:
    query = select(Book.id).where(
        Book.normalized_title == normalized_title, Book.normalized_author == normalized_author
    )
    if exclude_id is not None:
        query = query.where(Book.id != exclude_id)
    if db.scalar(query) is not None:
        raise DuplicateBook


@contextmanager
def book_write(db: Session) -> Iterator[None]:
    """Roll back failed book writes and translate known constraint failures."""
    try:
        yield
    except Exception as exc:
        db.rollback()
        if isinstance(exc, IntegrityError):
            constraint = (
                exc.orig.diag.constraint_name if isinstance(exc.orig, (UniqueViolation, ForeignKeyViolation)) else None
            )
            errors = {
                UNIQUE_BOOK_IDENTITY: DuplicateBook,
                UNIQUE_BOOK_COVER_FILE: file_service.FileAlreadyLinked,
                UNIQUE_BOOK_SOURCE_PDF_FILE: file_service.FileAlreadyLinked,
                FOREIGN_KEY_BOOK_COVER_FILE: file_service.FileNotFound,
                FOREIGN_KEY_BOOK_SOURCE_PDF_FILE: file_service.FileNotFound,
            }
            if constraint is not None and (error := errors.get(constraint)):
                raise error from exc
        raise


def check_file_links(db: Session, changes: dict[str, Any]) -> None:
    """Only persisted, successfully prepared files can be linked to a book."""
    for kind, field in file_service.BOOK_FILE_FIELDS.items():
        if (file_id := changes.get(field)) is not None:
            file_service.get_file(db, file_id, expected_kind=kind)


def list_books(db: Session, *, limit: int = 50, offset: int = 0) -> tuple[list[Book], int]:
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be 1..100 and offset must be nonnegative")
    query = select(Book).order_by(Book.id.asc())
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return list(db.scalars(query.limit(limit).offset(offset))), total or 0


def create_book(db: Session, payload: BookCreate) -> Book:
    with book_write(db):
        title = normalize_identity(payload.title)
        author = normalize_identity(payload.author)
        check_duplicate(db, title, author)
        check_file_links(db, payload.model_dump())
        now = datetime.now(UTC)
        book = Book(
            **payload.model_dump(),
            normalized_title=title,
            normalized_author=author,
            created_at=now,
            updated_at=now,
        )
        db.add(book)
        db.commit()
        return book


def patch_book(db: Session, book_id: int, payload: BookPatch) -> Book:
    with book_write(db):
        book = get_book(db, book_id, for_update=True)
        changes = payload.model_dump()

        has_changes = any(getattr(book, field) != value for field, value in changes.items())

        if not has_changes:
            return book

        title = normalize_identity(changes.get("title", book.title))
        author = normalize_identity(changes.get("author", book.author))
        check_duplicate(db, title, author, exclude_id=book.id)
        check_file_links(db, changes)

        # If files were updated for a book, delete the old files
        old_files: list[StoredFile] = []
        for field in file_service.BOOK_FILE_FIELDS.values():
            old_id = getattr(book, field)
            if field in changes and old_id is not None and old_id != changes[field]:
                old_files.append(file_service.get_file(db, old_id))

        for field, value in changes.items():
            setattr(book, field, value)

        book.normalized_title = title
        book.normalized_author = author
        book.updated_at = datetime.now(UTC)

        # Release the old FK and update with new ones before deleting the files.
        # If not, the delete will fail since the book table will have foreign keys pointed to these rows
        db.flush()
        for record in old_files:
            file_service.queue_file_deletion(db, record, now=book.updated_at)
        db.commit()

        return book


def delete_book(db: Session, book_id: int) -> bool:
    """Idempotent service operation; HTTP decides whether absence is a 404."""
    with book_write(db):
        book = db.get(Book, book_id, with_for_update=True, populate_existing=True)

        if book is None:
            return False

        old_files: list[StoredFile] = []

        for field in file_service.BOOK_FILE_FIELDS.values():
            if (file_id := getattr(book, field)) is not None:
                old_files.append(file_service.get_file(db, file_id))

        db.delete(book)
        # Same as the patch method - flush the book delete so that there's no foreign key pointing to the file rows
        db.flush()

        now = datetime.now(UTC)
        for record in old_files:
            file_service.queue_file_deletion(db, record, now=now)
        db.commit()
        return True
