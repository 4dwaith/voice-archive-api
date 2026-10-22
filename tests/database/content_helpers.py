"""Shared database setup for content and section-owned audio tests."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from voice_archive_api.book_files.model import FileKind, StoredFile
from voice_archive_api.content.model import BookSection, BookSegment


def add_section(db, book_id, **test_delta):
    values = dict(book_id=book_id, source_key="root", kind="chapter", title="Chapter", sibling_sequence=0)
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
