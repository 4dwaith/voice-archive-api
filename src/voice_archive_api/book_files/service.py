from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from voice_archive_api.book_files.covers import prepare_cover
from voice_archive_api.book_files.model import FileKind, FileStrategy, PendingFileDeletion, StoredFile
from voice_archive_api.book_files.pdfs import prepare_pdf
from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.file_handling import BinarySource, Storage

FILE_STRATEGIES = {
    FileKind.COVER: FileStrategy(prepare_cover, "cover_max_bytes", "image/webp", "cover.webp"),
    FileKind.SOURCE_PDF: FileStrategy(prepare_pdf, "pdf_max_bytes", "application/pdf", "source.pdf"),
}


BOOK_FILE_FIELDS = {
    FileKind.COVER: "cover_file_id",
    FileKind.SOURCE_PDF: "source_pdf_file_id",
}


class FileNotFound(ApplicationError):
    code = "file_not_found"
    message = "File not found"


class FileAlreadyLinked(ApplicationError):
    code = "file_already_linked"
    message = "File is already linked to a book"


class FileKindMismatch(ApplicationError):
    code = "file_kind_mismatch"
    message = "File kind does not match the book field"


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


def delete_file(db: Session, file_id: int) -> None:
    """Delete an unlinked row and queue its bytes, serializing with ownership updates."""
    with file_write(db):
        record = db.get(StoredFile, file_id, with_for_update=True, populate_existing=True)
        if record is None:
            raise FileNotFound
        if record.book_id is not None:
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
