"""Shared deletion of a book's content rows and owned file records."""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.file import StoredFile
from voice_archive_api.logic.files import FileKind
from voice_archive_api.services.files import service as files


def delete_book_content_and_files(db: Session, book_id: int, *, include_cover: bool = False) -> None:
    """Delete text/sections and PDF/audio records, optionally including the cover.

    The caller holds the book lock and owns the transaction. Physical byte deletion
    is durably queued; this helper neither commits nor deletes the Book row.
    """
    records = list(
        db.scalars(
            select(StoredFile)
            .where(
                ((StoredFile.book_id == book_id) & (True if include_cover else StoredFile.kind == FileKind.SOURCE_PDF))
                | StoredFile.section_id.in_(select(BookSection.id).where(BookSection.book_id == book_id))
            )
            .order_by(StoredFile.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    db.execute(delete(BookSegment).where(BookSegment.book_id == book_id))
    for record in records:
        record.book_id = None
        record.section_id = None
    db.flush()
    for record in records:
        files.queue_file_deletion(db, record, now=datetime.now(UTC))
    db.flush()
    db.execute(delete(BookSection).where(BookSection.book_id == book_id))
