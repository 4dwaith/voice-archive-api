from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from voice_archive_api.book_files.covers import prepare_cover
from voice_archive_api.book_files.model import CoverFile, PendingFileDeletion, SourcePdfFile, StoredFileBase
from voice_archive_api.book_files.pdfs import prepare_pdf
from voice_archive_api.books.model import Book
from voice_archive_api.file_handling import BinarySource, Storage


class CoverFileNotFound(Exception):
    pass


class SourcePdfFileNotFound(Exception):
    pass


class CoverFileAlreadyLinked(Exception):
    pass


class SourcePdfFileAlreadyLinked(Exception):
    pass


def queue_file_deletion(db: Session, record: StoredFileBase, *, now: datetime) -> None:
    """Remove a file record and queue its storage key for deletion in the caller's transaction."""
    db.add(PendingFileDeletion(storage_key=record.storage_key, queued_at=now))
    db.delete(record)


def get_cover_file(db: Session, file_id: int) -> CoverFile:
    record = db.get(CoverFile, file_id)
    if record is None:
        raise CoverFileNotFound
    return record


def get_pdf_file(db: Session, file_id: int) -> SourcePdfFile:
    record = db.get(SourcePdfFile, file_id)
    if record is None:
        raise SourcePdfFileNotFound
    return record


def delete_cover_file(db: Session, file_id: int) -> None:
    """Delete an unlinked cover record and queue its bytes for maintenance."""
    _delete_file(db, CoverFile, file_id, Book.cover_file_id, CoverFileNotFound, CoverFileAlreadyLinked)


def delete_pdf_file(db: Session, file_id: int) -> None:
    """Delete an unlinked PDF record and queue its bytes for maintenance."""
    _delete_file(db, SourcePdfFile, file_id, Book.source_pdf_file_id, SourcePdfFileNotFound, SourcePdfFileAlreadyLinked)


def _delete_file(db: Session, model, file_id: int, book_link, not_found, already_linked) -> None:
    try:
        # New FK references must wait until deletion commits or rolls back.
        record = db.get(model, file_id, with_for_update=True, populate_existing=True)
        if record is None:
            raise not_found
        if db.scalar(select(exists().where(book_link == file_id))):
            raise already_linked
        queue_file_deletion(db, record, now=datetime.now(UTC))
        db.commit()
    except Exception:
        db.rollback()
        raise


@contextmanager
def file_write(db: Session, storage: Storage, storage_key: str) -> Iterator[None]:
    """Share rollback and cleanup without combining the two file models."""
    try:
        yield
    except BaseException:
        try:
            db.rollback()
        finally:
            storage.delete(storage_key)
        raise


def create_cover_file(
    db: Session,
    source: BinarySource,
    storage: Storage,
    *,
    max_bytes: int,
) -> CoverFile:
    """Prepare and persist a cover independently of any book create/save."""
    prepared = prepare_cover(source, storage, max_bytes=max_bytes)
    with file_write(db, storage, prepared.storage_key):
        record = CoverFile(
            storage_key=prepared.storage_key,
            byte_size=prepared.byte_size,
            width=prepared.width,
            height=prepared.height,
            created_at=datetime.now(UTC),
        )
        db.add(record)
        db.commit()
        return record


def create_pdf_file(
    db: Session,
    source: BinarySource,
    storage: Storage,
    *,
    max_bytes: int,
) -> SourcePdfFile:
    """Prepare and persist a source PDF independently of any book create/save."""
    prepared = prepare_pdf(source, storage, max_bytes=max_bytes)
    with file_write(db, storage, prepared.storage_key):
        record = SourcePdfFile(
            storage_key=prepared.storage_key,
            byte_size=prepared.byte_size,
            page_count=prepared.page_count,
            created_at=datetime.now(UTC),
        )
        db.add(record)
        db.commit()
        return record
