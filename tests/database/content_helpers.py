"""Shared database setup for content and section-owned audio tests."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.file import StoredFile
from voice_archive_api.logic.files import FileKind


def add_section(db, book_id, **test_delta):
    previous = db.scalar(select(func.max(BookSection.sequence)).where(BookSection.book_id == book_id))
    values = dict(
        book_id=book_id,
        source_key="root",
        kind="chapter",
        title="Chapter",
        sibling_sequence=0,
        sequence=0 if previous is None else previous + 1,
    )
    values.update(test_delta)
    record = BookSection(**values)
    db.add(record)
    db.flush()
    return record


def add_segment(db, book_id, **test_delta):
    values = dict(
        book_id=book_id, source_key="s", kind="prose", html_content="<p>Text</p>", text_content="Text", sequence=0
    )
    values.update(test_delta)
    record = BookSegment(**values)
    db.add(record)
    db.flush()
    return record


def add_audio(db, **test_delta):
    values = dict(
        kind=FileKind.AUDIO_CHAPTER,
        storage_key=str(uuid4()),
        byte_size=1,
        file_metadata={"duration_ms": 1000},
        created_at=datetime.now(UTC) - timedelta(days=30),
    )
    values.update(test_delta)
    record = StoredFile(**values)
    db.add(record)
    db.flush()
    return record
