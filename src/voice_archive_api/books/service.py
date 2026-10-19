from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from psycopg.errors import UniqueViolation
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from voice_archive_api.books.constants import UNIQUE_BOOK_IDENTITY
from voice_archive_api.books.model import Book
from voice_archive_api.books.schemas import BookCreate, BookPatch
from voice_archive_api.books.validation import normalize_identity


class BookNotFound(Exception):
    pass


class DuplicateBook(Exception):
    pass


def get_book(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if book is None:
        raise BookNotFound
    return book


def check_duplicate(
        db: Session,
        normalized_title: str,
        normalized_author: str,
        exclude_id: int | None = None
) -> None:
    query = select(Book.id).where(
        Book.normalized_title == normalized_title,
        Book.normalized_author == normalized_author
    )
    if exclude_id is not None:
        query = query.where(Book.id != exclude_id)
    if db.scalar(query) is not None:
        raise DuplicateBook


@contextmanager
def book_write(db: Session) -> Iterator[None]:
    """Roll back failed book writes and translate duplicate identity errors."""
    try:
        yield
    except Exception as exc:
        db.rollback()
        # Translate only this unique constraint, never unrelated data failures.
        if (
                isinstance(exc, IntegrityError)
                and isinstance(exc.orig, UniqueViolation)
                and exc.orig.diag.constraint_name == UNIQUE_BOOK_IDENTITY
        ):
            raise DuplicateBook from exc
        raise


def list_books(
        db: Session, *, limit: int = 50, offset: int = 0
) -> tuple[list[Book], int]:
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
        book = get_book(db, book_id)
        changes = payload.model_dump()

        has_changes = any(getattr(book, field) != value for field, value in changes.items())

        if not has_changes:
            return book

        title = normalize_identity(changes.get("title", book.title))
        author = normalize_identity(changes.get("author", book.author))
        check_duplicate(db, title, author, exclude_id=book.id)

        for field, value in changes.items():
            setattr(book, field, value)

        book.normalized_title = title
        book.normalized_author = author
        book.updated_at = datetime.now(UTC)
        db.commit()

        return book


def delete_book(db: Session, book_id: int) -> bool:
    """Idempotent service operation; HTTP decides whether absence is a 404."""
    with book_write(db):
        book = db.get(Book, book_id)
        if book is None:
            return False
        db.delete(book)
        db.commit()
        return True
