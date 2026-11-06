import httpx
from google.genai import errors
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from voice_archive_api.core.config import Settings
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.logic.library import BookSnapshot, EvidenceSegment, SourceSegment
from voice_archive_api.services.content.reads import lock_book
from voice_archive_api.services.library import provider
from voice_archive_api.services.library.schemas import AnswerReference, AskRequest, AskResult


def read_book_snapshot(factory: sessionmaker[Session], book_id: int) -> BookSnapshot | None:
    with factory.begin() as db:
        book = lock_book(db, book_id)
        if book.content_version is None:
            return None
        sections = {row.id: row for row in db.scalars(select(BookSection).where(BookSection.book_id == book_id))}
        segments = []
        for row in db.scalars(select(BookSegment).where(BookSegment.book_id == book_id).order_by(BookSegment.sequence)):
            path = []
            section_id = row.section_id
            while section_id is not None:
                section = sections[section_id]
                path.append(section.title)
                section_id = section.parent_id
            segments.append(
                SourceSegment(
                    segment_id=row.id,
                    section_id=row.section_id,
                    path=[book.title, *reversed(path)],
                    sequence=row.sequence,
                    text=row.text_content,
                )
            )
        return BookSnapshot(book_id=book.id, content_version=book.content_version, title=book.title, segments=segments)


def is_source_current(factory: sessionmaker[Session], snapshot: BookSnapshot) -> bool:
    with factory.begin() as db:
        return db.scalar(select(Book.content_version).where(Book.id == snapshot.book_id)) == snapshot.content_version


async def ask_book(factory: sessionmaker[Session], settings: Settings, request: AskRequest) -> AskResult:
    snapshot = await run_in_threadpool(read_book_snapshot, factory, request.book_id)
    if snapshot is None or not snapshot.segments:
        return AskResult(
            status="unavailable", answer="", limitations=["This book has no published text to answer from."]
        )
    try:
        evidence = [
            EvidenceSegment(segment_id=segment.segment_id, path=segment.path, text=segment.text)
            for segment in snapshot.segments
        ]
        draft = await provider.generate_answer(settings, request.query, evidence, request.effort)
        segments = {segment.segment_id: segment for segment in snapshot.segments}
        references = [
            AnswerReference(
                **segments[reference.segment_id].model_dump(),
                book_id=snapshot.book_id,
                content_version=snapshot.content_version,
                relevance=reference.relevance,
            )
            for reference in draft.references
        ]
    except provider.BookTooLargeError:
        return AskResult(
            status="oversize", answer="", limitations=["The complete book exceeds the model input budget."]
        )
    except errors.APIError, httpx.HTTPError, TimeoutError, ValueError, KeyError:
        return AskResult(status="unavailable", answer="", limitations=["An answer is unavailable; please retry later."])
    if not await run_in_threadpool(is_source_current, factory, snapshot):
        return AskResult(status="stale", answer="", limitations=["Book content changed while answering; ask again."])
    return AskResult(status=draft.status, answer=draft.answer, limitations=draft.limitations, references=references)
