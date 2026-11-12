from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.series import Series
from voice_archive_api.db.operations import read_constraint_name
from voice_archive_api.services.access import can_access_resource, create_owner_filter, get_access_context
from voice_archive_api.services.books.errors import BookNotFound
from voice_archive_api.services.series.schemas import SeriesCreate, SeriesPatch


class SeriesOrderConflict(ApplicationError):
    code = "series_order_conflict"
    message = "Series indices must be continuous; use the series books endpoint to replace membership and order"


class SeriesNotFound(ApplicationError):
    code = "series_not_found"
    message = "Series not found"


def get_series(db: Session, series_id: int, *, for_update: bool = False) -> Series:
    series = db.get(Series, series_id, with_for_update=for_update, populate_existing=for_update)
    if series is None or not can_access_resource(db, series.owner_user_id):
        raise SeriesNotFound
    return series


def list_series(db: Session, *, limit: int = 50, offset: int = 0) -> tuple[list[Series], int]:
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be 1..100 and offset must be nonnegative")
    condition = create_owner_filter(db, Series.owner_user_id)
    items = db.scalars(select(Series).where(condition).order_by(Series.id).limit(limit).offset(offset))
    return list(items), db.scalar(select(func.count()).select_from(Series).where(condition)) or 0


def create_series(db: Session, payload: SeriesCreate) -> Series:
    now = datetime.now(UTC)
    access = get_access_context(db)
    series = Series(title=payload.title, owner_user_id=access.user_id, created_at=now, updated_at=now)
    db.add(series)
    db.commit()
    db.refresh(series)
    return series


def patch_series(db: Session, series_id: int, payload: SeriesPatch) -> Series:
    series = get_series(db, series_id, for_update=True)
    changes = payload.model_dump()
    if "title" in changes and changes["title"] != series.title:
        series.title = changes["title"]
        series.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(series)
    return series


def delete_series(db: Session, series_id: int) -> None:
    series = get_series(db, series_id, for_update=True)
    for book in lock_series_books(db, series_id):
        book.series_id = book.series_index = None
        book.updated_at = datetime.now(UTC)
    db.flush()
    db.delete(series)
    db.commit()


def list_member_book_ids(db: Session, series_id: int) -> list[int]:
    return list(db.scalars(select(Book.id).where(Book.series_id == series_id).order_by(Book.series_index)))


def lock_series_books(db: Session, series_id: int, book_ids: list[int] | None = None) -> list[Book]:
    return list(
        db.scalars(
            select(Book)
            .where(or_(Book.series_id == series_id, Book.id.in_(book_ids or [])))
            .where(create_owner_filter(db, Book.owner_user_id))
            .order_by(Book.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def replace_series_books(db: Session, series_id: int, book_ids: list[int]) -> Series:
    try:
        series = get_series(db, series_id, for_update=True)
        books = lock_series_books(db, series_id, book_ids)
        by_id = {book.id: book for book in books}
        if any(book_id not in by_id or by_id[book_id].owner_user_id != series.owner_user_id for book_id in book_ids):
            raise BookNotFound
        if len(set(book_ids)) != len(book_ids) or any(
            by_id[book_id].series_id not in (None, series_id) for book_id in book_ids
        ):
            raise SeriesOrderConflict
        positions = {book_id: index for index, book_id in enumerate(book_ids, start=1)}
        now = datetime.now(UTC)
        for book in books:
            index = positions.get(book.id)
            if book.series_index != index or book.series_id != (series_id if index else None):
                book.series_id = series_id if index else None
                book.series_index = index
                book.updated_at = now
        db.commit()
        db.refresh(series)
        return series
    except Exception as exc:
        db.rollback()
        if isinstance(exc, IntegrityError) and read_constraint_name(exc) == "fk_books_series_ownership":
            raise BookNotFound from exc
        raise
