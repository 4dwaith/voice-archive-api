from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from voice_archive_api.book_files.covers import prepare_cover
from voice_archive_api.book_files.model import CoverFile, SourcePdfFile
from voice_archive_api.book_files.pdfs import prepare_pdf
from voice_archive_api.file_handling import BinarySource, Storage


class CoverFileNotFound(Exception):
    pass


class SourcePdfFileNotFound(Exception):
    pass


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
    db: Session, source: BinarySource, storage: Storage, *, max_bytes: int,
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
    db: Session, source: BinarySource, storage: Storage, *, max_bytes: int,
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
