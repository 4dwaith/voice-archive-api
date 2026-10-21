from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from voice_archive_api.book_files.covers import prepare_cover
from voice_archive_api.book_files.model import FileKind, FileStrategy, PendingFileDeletion, StoredFile
from voice_archive_api.book_files.pdfs import prepare_pdf
from voice_archive_api.books.model import Book
from voice_archive_api.file_handling import BinarySource, Storage


FILE_STRATEGIES = {
    FileKind.COVER: FileStrategy(prepare_cover, "cover_max_bytes", "image/webp", "cover.webp"),
    FileKind.SOURCE_PDF: FileStrategy(prepare_pdf, "pdf_max_bytes", "application/pdf", "source.pdf"),
}


BOOK_FILE_FIELDS = {
    FileKind.COVER: "cover_file_id",
    FileKind.SOURCE_PDF: "source_pdf_file_id",
}


class FileNotFound(Exception):
    pass


class FileAlreadyLinked(Exception):
    pass


class FileKindMismatch(Exception):
    pass


def queue_file_deletion(db: Session, record: StoredFile, *, now: datetime) -> None:
    """Remove the row and queue bytes for deletion in the caller's transaction."""
    db.add(PendingFileDeletion(storage_key=record.storage_key, queued_at=now))
    db.delete(record)


def get_file(db: Session, file_id: int, *, expected_kind: FileKind | None = None) -> StoredFile:
    record = db.get(StoredFile, file_id)
    if record is None:
        raise FileNotFound
    if expected_kind is not None and record.kind != expected_kind:
        raise FileKindMismatch
    return record


def file_is_unlinked(file_id: int | InstrumentedAttribute[int] = StoredFile.id):
    """Build independent NOT EXISTS checks so cleanup can use indexed anti joins."""
    return and_(
        *(~exists(select(Book.id).where(getattr(Book, field) == file_id)) for field in BOOK_FILE_FIELDS.values())
    )


def delete_file(db: Session, file_id: int) -> None:
    """Delete an unlinked row and queue its bytes, serializing with FK claims."""
    with file_write(db):
        record = db.get(StoredFile, file_id, with_for_update=True, populate_existing=True)
        if record is None:
            raise FileNotFound
        if not db.scalar(select(file_is_unlinked(file_id))):
            raise FileAlreadyLinked
        queue_file_deletion(db, record, now=datetime.now(UTC))
        db.commit()


@contextmanager
def file_write(db: Session) -> Iterator[None]:
    try:
        yield
    except BaseException:
        db.rollback()
        raise


def create_file(
    db: Session,
    kind: FileKind,
    source: BinarySource,
    storage: Storage,
    *,
    max_bytes: int,
) -> StoredFile:
    prepared = FILE_STRATEGIES[kind].prepare(source, storage, max_bytes=max_bytes)
    with file_write(db):
        record = StoredFile(
            kind=kind,
            storage_key=prepared.storage_key,
            byte_size=prepared.byte_size,
            file_metadata=prepared.metadata,
            created_at=datetime.now(UTC),
        )
        db.add(record)
        # Preserve promoted bytes on an ambiguous commit; orphan maintenance reclaims them.
        db.commit()
        return record
