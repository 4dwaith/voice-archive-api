"""Version-pinned, bounded reads; shared book locks prevent purge during a response."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.file import StoredFile
from voice_archive_api.services.books.service import BookNotFound
from voice_archive_api.services.content.schemas import SectionPage, SectionRead, SegmentPage, SegmentRead


class ContentVersionMismatch(ApplicationError):
    code = "content_version_mismatch"
    message = "Book content was removed or changed; reopen the book from the library"


def lock_book(db: Session, book_id: int, version: UUID | None = None) -> Book:
    book = db.scalar(
        select(Book).where(Book.id == book_id).with_for_update(read=True).execution_options(populate_existing=True)
    )
    if book is None:
        raise BookNotFound
    if version is not None and book.content_version != version:
        raise ContentVersionMismatch
    return book


def sections(db: Session, book: Book, limit: int, offset: int) -> SectionPage:
    assert book.content_version is not None
    rows = list(
        db.scalars(
            select(BookSection)
            .where(BookSection.book_id == book.id)
            .order_by(BookSection.sequence)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    # Structural projections are needed only for descendant navigation targets;
    # pagination itself uses the stored sequence directly in SQL.
    structure = list(
        db.execute(
            select(BookSection.id, BookSection.parent_id)
            .where(BookSection.book_id == book.id)
            .order_by(BookSection.sequence)
        )
    )
    first: dict[int, tuple[int, str]] = {}
    for section_id, source_key, sequence in db.execute(
        select(BookSegment.section_id, BookSegment.source_key, BookSegment.sequence)
        .distinct(BookSegment.section_id)
        .where(BookSegment.book_id == book.id, BookSegment.section_id.is_not(None))
        .order_by(BookSegment.section_id, BookSegment.sequence)
    ):
        first[section_id] = (sequence, source_key)
    # Reverse traversal propagates the earliest document segment through empty parents.
    for section_id, parent_id in reversed(structure):
        if section_id in first and parent_id is not None:
            previous = first.get(parent_id)
            if previous is None or first[section_id][0] < previous[0]:
                first[parent_id] = first[section_id]
    audio: dict[int | None, int] = dict(
        db.execute(
            select(StoredFile.section_id, StoredFile.id).where(
                StoredFile.section_id.in_(select(BookSection.id).where(BookSection.book_id == book.id))
            )
        )
        .tuples()
        .all()
    )
    items = [
        SectionRead(
            id=r.id,
            source_key=r.source_key,
            parent_id=r.parent_id,
            kind=r.kind,
            title=r.title,
            sibling_sequence=r.sibling_sequence,
            sequence=r.sequence,
            audio_file_id=audio.get(r.id),
            first_segment_source_key=first[r.id][1] if r.id in first else None,
        )
        for r in rows[:limit]
    ]
    return SectionPage(
        content_version=book.content_version,
        items=items,
        next_offset=offset + len(items) if len(rows) > limit else None,
    )


def segments(db: Session, book: Book, limit: int, offset: int) -> SegmentPage:
    assert book.content_version is not None
    rows = list(
        db.scalars(
            select(BookSegment)
            .where(BookSegment.book_id == book.id)
            .order_by(BookSegment.sequence)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    items = [SegmentRead.model_validate(row) for row in rows[:limit]]
    return SegmentPage(
        content_version=book.content_version,
        items=items,
        next_offset=offset + len(items) if len(rows) > limit else None,
    )
