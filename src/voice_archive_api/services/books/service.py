from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.db.constants import FOREIGN_KEY_BOOK_SERIES, UNIQUE_BOOK_IDENTITY, UNIQUE_BOOK_SERIES_INDEX
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.file import StoredFile
from voice_archive_api.db.operations import read_constraint_name
from voice_archive_api.logic.books.validation import normalize_identity
from voice_archive_api.logic.files import FileKind
from voice_archive_api.services.access import (
    can_access_resource,
    create_file_access_filter,
    create_owner_filter,
    get_access_context,
)
from voice_archive_api.services.book_cleanup import delete_book_content_and_files
from voice_archive_api.services.books.errors import BookNotFound as BookNotFound
from voice_archive_api.services.books.schemas import BookCreate, BookPatch
from voice_archive_api.services.files import service as file_service
from voice_archive_api.services.series.service import SeriesNotFound, SeriesOrderConflict, get_series


class DuplicateBook(ApplicationError):
    code = "duplicate_book"
    message = "A book with this title and author already exists"


class ContentPurgeRequired(ApplicationError):
    code = "content_purge_required"
    message = "Purge imported book content before changing its source PDF"


def get_book(db: Session, book_id: int, *, for_update: bool = False) -> Book:
    book = db.get(Book, book_id, with_for_update=for_update, populate_existing=for_update)
    if book is None or not can_access_resource(db, book.owner_user_id):
        raise BookNotFound
    if for_update:
        # Refresh scalar file-owner projections after a possibly blocking lock wait.
        db.refresh(book)
    return book


def check_duplicate(
    db: Session,
    normalized_title: str,
    normalized_author: str,
    exclude_id: int | None = None,
    *,
    owner_user_id: int | None = None,
) -> None:
    query = select(Book.id).where(
        Book.normalized_title == normalized_title,
        Book.normalized_author == normalized_author,
        Book.owner_user_id == owner_user_id,
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
            constraint = read_constraint_name(exc)
            errors = {
                UNIQUE_BOOK_IDENTITY: DuplicateBook,
                FOREIGN_KEY_BOOK_SERIES: SeriesNotFound,
                "fk_books_series_ownership": SeriesNotFound,
                UNIQUE_BOOK_SERIES_INDEX: SeriesOrderConflict,
            }
            if constraint is not None and (error := errors.get(constraint)):
                raise error from exc
        raise


def lock_book_for_series_write(db: Session, book_id: int, changes: dict[str, Any]) -> Book | None:
    # Read membership before taking locks, then recheck it after locking parents
    # in ID order and the book. Never acquire a series lock while holding a book lock.
    snapshot = db.execute(
        select(Book.series_id).where(Book.id == book_id, create_owner_filter(db, Book.owner_user_id))
    ).one_or_none()
    if snapshot is None:
        return None
    source_id = snapshot[0]
    target_id = changes.get("series_id", source_id)
    for series_id in sorted({value for value in (source_id, target_id) if value is not None}):
        get_series(db, series_id, for_update=True)
    book = get_book(db, book_id, for_update=True)
    if book.series_id != source_id:
        raise SeriesOrderConflict
    return book


def validate_series_membership(db: Session, book: Book | None, changes: dict[str, Any]) -> None:
    source_id = None
    source_index = None
    owner_user_id = get_access_context(db).user_id
    if book is not None:
        source_id = book.series_id
        source_index = book.series_index
        owner_user_id = book.owner_user_id
    target_id = changes.get("series_id", source_id)
    if target_id is not None and get_series(db, target_id).owner_user_id != owner_user_id:
        raise SeriesNotFound
    if target_id == source_id:
        if changes.get("series_index", source_index) != source_index:
            raise SeriesOrderConflict
        return
    if source_id is not None and source_index != db.scalar(
        select(func.count()).select_from(Book).where(Book.series_id == source_id)
    ):
        raise SeriesOrderConflict
    expected_index = (
        None
        if target_id is None
        else 1 + (db.scalar(select(func.count()).select_from(Book).where(Book.series_id == target_id)) or 0)
    )
    if changes.get("series_index", expected_index) != expected_index:
        raise SeriesOrderConflict
    changes.update(series_id=target_id, series_index=expected_index)


def check_book_links(db: Session, changes: dict[str, Any]) -> None:
    """Validate referenced series and persisted, successfully prepared files."""
    if (series_id := changes.get("series_id")) is not None:
        get_series(db, series_id)
    for kind, field in file_service.BOOK_FILE_FIELDS.items():
        if (file_id := changes.get(field)) is not None:
            file_service.get_file(db, file_id, expected_kind=kind)


def list_books(db: Session, *, limit: int = 50, offset: int = 0) -> tuple[list[Book], int]:
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be 1..100 and offset must be nonnegative")
    query = select(Book).where(create_owner_filter(db, Book.owner_user_id)).order_by(Book.id.asc())
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
            create_file_access_filter(db),
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
        owner_user_id = get_access_context(db).user_id
        check_duplicate(db, title, author, owner_user_id=owner_user_id)
        changes = payload.model_dump()
        if "series_index" not in payload.model_fields_set:
            changes.pop("series_index")
        if payload.series_id is not None:
            get_series(db, payload.series_id, for_update=True)
        validate_series_membership(db, None, changes)
        check_book_links(db, changes)
        now = datetime.now(UTC)
        book = Book(
            owner_user_id=owner_user_id,
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
        changes = payload.model_dump()
        book = lock_book_for_series_write(db, book_id, changes)
        if book is None:
            raise BookNotFound
        validate_series_membership(db, book, changes)
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
        check_duplicate(db, title, author, exclude_id=book.id, owner_user_id=book.owner_user_id)
        check_book_links(db, changes)
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
        book = lock_book_for_series_write(db, book_id, {"series_id": None})
        if book is None:
            return False
        validate_series_membership(db, book, {"series_id": None})
        delete_book_content_and_files(db, book_id, include_cover=True)
        db.delete(book)
        db.commit()
        return True
