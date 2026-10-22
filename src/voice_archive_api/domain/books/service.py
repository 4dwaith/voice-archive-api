from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from psycopg.errors import ForeignKeyViolation, UniqueViolation
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.domain.book_cleanup import delete_book_content_and_files
from voice_archive_api.domain.books.constants import (
    UNIQUE_BOOK_IDENTITY,
)
from voice_archive_api.domain.books.model import Book
from voice_archive_api.domain.books.schemas import BookCreate, BookPatch
from voice_archive_api.domain.books.validation import normalize_identity
from voice_archive_api.domain.files import service as file_service
from voice_archive_api.domain.files.model import FileKind, StoredFile


class BookNotFound(ApplicationError):
    code = "book_not_found"
    message = "Book not found"


class DuplicateBook(ApplicationError):
    code = "duplicate_book"
    message = "A book with this title and author already exists"


class ContentPurgeRequired(ApplicationError):
    code = "content_purge_required"
    message = "Purge imported book content before changing its source PDF"


def get_book(db: Session, book_id: int, *, for_update: bool = False) -> Book:
    book = db.get(Book, book_id, with_for_update=for_update, populate_existing=for_update)
    if book is None:
        raise BookNotFound
    if for_update:
        # Refresh scalar file-owner projections after a possibly blocking lock wait.
        db.refresh(book)
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


def claim_file(db: Session, file_id: int, *, book_id: int, kind: FileKind) -> None:
    """Claim only an unowned file, checking the current row after concurrent writes."""
    claimed = db.scalar(
        update(StoredFile)
        .where(
            StoredFile.id == file_id,
            StoredFile.book_id.is_(None),
            StoredFile.section_id.is_(None),
            StoredFile.kind == kind,
        )
        .values(book_id=book_id)
        .returning(StoredFile.id)
        .execution_options(synchronize_session=False)
    )
    if claimed is None:
        # A previous lookup may have raced with deletion or another claim.
        db.expire_all()

        # Raise errors if the file is deleted or of a different kind.
        file_service.get_file(db, file_id, expected_kind=kind)
        raise file_service.FileAlreadyLinked


def create_book(db: Session, payload: BookCreate) -> Book:
    with book_write(db):
        title = normalize_identity(payload.title)
        author = normalize_identity(payload.author)
        check_duplicate(db, title, author)
        changes = payload.model_dump()
        check_file_links(db, changes)
        now = datetime.now(UTC)
        book = Book(
            **{field: value for field, value in changes.items() if field not in file_service.BOOK_FILE_FIELDS.values()},
            normalized_title=title,
            normalized_author=author,
            created_at=now,
            updated_at=now,
        )
        db.add(book)
        db.flush()
        for kind, field in file_service.BOOK_FILE_FIELDS.items():
            if (file_id := changes.get(field)) is not None:
                claim_file(db, file_id, book_id=book.id, kind=kind)
        db.commit()
        db.refresh(book)
        return book


def patch_book(db: Session, book_id: int, payload: BookPatch) -> Book:
    with book_write(db):
        book = get_book(db, book_id, for_update=True)
        changes = payload.model_dump()
        if (
            "source_pdf_file_id" in changes
            and changes["source_pdf_file_id"] != book.source_pdf_file_id
            and book.content_version is not None
        ):
            raise ContentPurgeRequired
        if not any(getattr(book, field) != value for field, value in changes.items()):
            return book

        title = normalize_identity(changes.get("title", book.title))
        author = normalize_identity(changes.get("author", book.author))
        check_duplicate(db, title, author, exclude_id=book.id)
        check_file_links(db, changes)
        now = datetime.now(UTC)

        for kind, field in file_service.BOOK_FILE_FIELDS.items():
            old_file_id = getattr(book, field)
            if field not in changes or old_file_id == changes[field]:
                continue

            if old_file_id is not None:
                # Detach before deleting: the database rejects deletion of linked files.
                # Ownership, row deletion, and the byte-deletion request commit together.
                record = db.get(StoredFile, old_file_id, with_for_update=True, populate_existing=True)
                if record is not None and record.book_id == book.id:
                    record.book_id = None
                    db.flush()
                    file_service.queue_file_deletion(db, record, now=now)
                    db.flush()

            if (file_id := changes[field]) is not None:
                claim_file(db, file_id, book_id=book.id, kind=kind)

        for field, value in changes.items():
            if field not in file_service.BOOK_FILE_FIELDS.values():
                setattr(book, field, value)

        book.normalized_title = title
        book.normalized_author = author
        book.updated_at = now
        db.commit()
        db.refresh(book)
        return book


def delete_book(db: Session, book_id: int) -> bool:
    """Idempotent service operation; HTTP decides whether absence is a 404."""
    with book_write(db):
        book = db.get(Book, book_id, with_for_update=True, populate_existing=True)
        if book is None:
            return False
        delete_book_content_and_files(db, book_id, include_cover=True)
        db.delete(book)
        db.commit()
        return True
