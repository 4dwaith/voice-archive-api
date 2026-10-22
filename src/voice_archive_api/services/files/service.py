from collections.abc import Collection, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import MappingProxyType

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.db.models.file import PendingFileDeletion, StoredFile
from voice_archive_api.file_handling import BinarySource, Storage
from voice_archive_api.logic.files import FileKind, PreparedAsset
from voice_archive_api.services.files.audio import prepare_audio
from voice_archive_api.services.files.covers import prepare_cover
from voice_archive_api.services.files.pdfs import prepare_pdf
from voice_archive_api.services.files.types import FileStrategy

FILE_STRATEGIES = {
    FileKind.COVER: FileStrategy(prepare_cover, "cover_max_bytes", "image/webp", "cover.webp"),
    FileKind.SOURCE_PDF: FileStrategy(prepare_pdf, "pdf_max_bytes", "application/pdf", "source.pdf"),
    FileKind.AUDIO_CHAPTER: FileStrategy(prepare_audio, "audio_max_bytes", "audio/mpeg", "chapter.mp3"),
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
    message = "File is already linked to an owner"


class FileKindMismatch(ApplicationError):
    code = "file_kind_mismatch"
    message = "File kind does not match the owner field"


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


def get_prepared_assets(
    db: Session, file_ids: Collection[int], *, for_update: bool = False
) -> dict[int, PreparedAsset]:
    """Read prepared-file snapshots in one query; omit missing IDs for caller diagnostics.

    This does not claim owners or commit. Publication requests for_update=True
    to lock/recheck files in ascending ID order (to avoid deadlocks) before claiming them.
    """
    if not file_ids:
        return {}
    query = (
        select(StoredFile.id, StoredFile.kind, StoredFile.book_id, StoredFile.section_id, StoredFile.file_metadata)
        .where(StoredFile.id.in_(file_ids))
        .order_by(StoredFile.id)
    )
    if for_update:
        query = query.with_for_update()
    records = db.execute(query)
    return {
        file_id: PreparedAsset(file_id, kind, book_id, section_id, MappingProxyType(dict(metadata)))
        for file_id, kind, book_id, section_id, metadata in records
    }


def delete_file(db: Session, file_id: int) -> None:
    """Delete an unlinked row and queue its bytes, serializing with ownership updates."""
    with file_write(db):
        record = db.get(StoredFile, file_id, with_for_update=True, populate_existing=True)
        if record is None:
            raise FileNotFound
        if record.book_id is not None or record.section_id is not None:
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
    prepare = FILE_STRATEGIES[kind].prepare
    if prepare is None:
        raise ValueError(f"Preparation is not available for {kind}")
    prepared = prepare(source, storage, max_bytes=max_bytes)
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


def claim_section_audio(db: Session, file_id: int, *, section_id: int) -> None:
    """Claim an unowned prepared audio row in the caller's transaction."""
    claimed = db.scalar(
        update(StoredFile)
        .where(
            StoredFile.id == file_id,
            StoredFile.book_id.is_(None),
            StoredFile.section_id.is_(None),
            StoredFile.kind == FileKind.AUDIO_CHAPTER,
        )
        .values(section_id=section_id)
        .returning(StoredFile.id)
        .execution_options(synchronize_session=False)
    )
    if claimed is None:
        db.expire_all()
        get_file(db, file_id, expected_kind=FileKind.AUDIO_CHAPTER)
        raise FileAlreadyLinked
