"""Scope checks, multi-channel window retrieval and bounded canonical evidence reads."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session, sessionmaker

from voice_archive_api.core.config import Settings
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.series import Series
from voice_archive_api.db.models.windows import RetrievalWindow, WindowEmbedding
from voice_archive_api.logic.books.validation import resolve_primary_language
from voice_archive_api.logic.library import EvidenceSegment
from voice_archive_api.logic.search.retrieval import WindowMatch
from voice_archive_api.services.access import create_owner_filter, get_access_context
from voice_archive_api.services.books.service import BookNotFound
from voice_archive_api.services.library.schemas import AskRequest
from voice_archive_api.services.library.search.dense import search_dense_windows
from voice_archive_api.services.library.search.lexical import search_lexical_windows
from voice_archive_api.services.series.service import SeriesNotFound

MAX_EVIDENCE_BYTES = 8_000_000


class SourceChangedError(ValueError):
    pass


class EvidenceTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class BookSource:
    id: int
    content_version: UUID | None
    title: str
    language: str
    series_id: int | None
    series_index: int | None
    series_title: str | None
    has_text: bool


@dataclass(frozen=True)
class LibrarySourceSnapshot:
    books: list[BookSource]
    series: tuple[int, str] | None


def read_scope(db: Session, request: AskRequest) -> LibrarySourceSnapshot:
    has_text = select(BookSegment.id).where(BookSegment.book_id == Book.id).exists()
    statement = select(
        Book.id,
        Book.content_version,
        Book.title,
        Book.language,
        Book.series_id,
        Book.series_index,
        Book.series_title,
        has_text.label("has_text"),
    ).where(create_owner_filter(db, Book.owner_user_id))
    if request.book_id is not None:
        statement = statement.where(Book.id == request.book_id)
    elif request.series_id is not None:
        statement = statement.where(Book.series_id == request.series_id)
    books = [
        BookSource(**row._mapping) for row in db.execute(statement.order_by(Book.series_id, Book.series_index, Book.id))
    ]
    if request.book_id is not None and not books:
        raise BookNotFound
    series = (
        db.execute(
            select(Series.id, Series.title).where(
                Series.id == request.series_id, create_owner_filter(db, Series.owner_user_id)
            )
        ).first()
        if request.series_id is not None
        else None
    )
    if request.series_id is not None and series is None and not get_access_context(db).is_trusted:
        raise SeriesNotFound
    return LibrarySourceSnapshot(books, (series.id, series.title) if series else None)


def require_current_source(db: Session, request: AskRequest, snapshot: LibrarySourceSnapshot) -> None:
    try:
        if read_scope(db, request) == snapshot:
            return
    except BookNotFound, SeriesNotFound:
        pass
    raise SourceChangedError


def read_source_snapshot(factory: sessionmaker[Session], request: AskRequest) -> LibrarySourceSnapshot:
    with factory.begin() as db:
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        return read_scope(db, request)


def read_source_availability(
    factory: sessionmaker[Session],
    settings: Settings,
    request: AskRequest,
) -> tuple[LibrarySourceSnapshot, set[int]]:
    with factory.begin() as db:
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        snapshot = read_scope(db, request)
        return snapshot, find_unprepared_book_ids(db, settings, snapshot.books)


def find_unprepared_book_ids(db: Session, settings: Settings, books: list[BookSource]) -> set[int]:
    book_ids = [book.id for book in books]
    scoped_windows = (
        select(RetrievalWindow.id, RetrievalWindow.book_id)
        .join(Book, Book.id == RetrievalWindow.book_id)
        .where(
            RetrievalWindow.book_id.in_(book_ids),
            RetrievalWindow.content_version == Book.content_version,
        )
        .subquery()
    )
    window_counts = {
        book_id: count
        for book_id, count in db.execute(
            select(scoped_windows.c.book_id, func.count()).group_by(scoped_windows.c.book_id),
        )
    }
    coverage = {
        (book_id, kind): count
        for book_id, kind, count in db.execute(
            select(scoped_windows.c.book_id, WindowEmbedding.kind, func.count())
            .join(
                WindowEmbedding,
                WindowEmbedding.window_id == scoped_windows.c.id,
            )
            .where(
                WindowEmbedding.model == settings.gemini_embedding_model,
                WindowEmbedding.dimensions == settings.gemini_embedding_dimensions,
                WindowEmbedding.input_format == settings.embedding_input_format,
                WindowEmbedding.vector.is_not(None),
                func.vector_norm(WindowEmbedding.vector) > 0,
            )
            .group_by(scoped_windows.c.book_id, WindowEmbedding.kind)
        ).all()
    }
    missing = set()
    for book in books:
        count = window_counts.get(book.id, 0)
        kinds = ("primary",) if resolve_primary_language(book.language) == "en" else ("primary", "translated")
        if (
            book.content_version is None
            or not book.has_text
            or not count
            or any(coverage.get((book.id, kind), 0) != count for kind in kinds)
        ):
            missing.add(book.id)
    return missing


def search_windows(
    factory: sessionmaker[Session],
    settings: Settings,
    request: AskRequest,
    snapshot: LibrarySourceSnapshot,
    routes: dict[tuple[str, str], list[int]],
    vectors: dict[str, list[float]],
) -> tuple[dict[str, list[WindowMatch]], set[int]]:
    with factory.begin() as db:
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        require_current_source(db, request, snapshot)
        unprepared = find_unprepared_book_ids(db, settings, snapshot.books)
        ranked_lists: dict[str, list[WindowMatch]] = {}
        for (kind, query), book_ids in routes.items():
            eligible = [book_id for book_id in book_ids if book_id not in unprepared]
            ranked_lists.setdefault(kind, []).extend(search_dense_windows(db, settings, eligible, kind, vectors[query]))
        for is_english in (True, False):
            eligible = [
                book.id
                for book in snapshot.books
                if book.id not in unprepared and (resolve_primary_language(book.language) == "en") == is_english
            ]
            if eligible:
                ranked_lists[f"lexical_{is_english}"] = search_lexical_windows(
                    db,
                    eligible,
                    is_english,
                    (request.query_en or "") if is_english else request.query,
                    request.lexical_terms,
                )
        return ranked_lists, unprepared


def require_evidence_size(
    db: Session, settings: Settings, book_ids: list[int], segment_ids: list[int], *, use_whole_book: bool
) -> None:
    size = db.scalar(
        select(func.coalesce(func.sum(func.octet_length(BookSegment.text_content) + 1024), 0)).where(
            BookSegment.book_id.in_(book_ids),
            true() if use_whole_book else BookSegment.id.in_(segment_ids),
        )
    )
    if size is not None and size > min(MAX_EVIDENCE_BYTES, settings.gemini_answer_input_tokens * 8):
        raise EvidenceTooLargeError


def read_evidence(
    factory: sessionmaker[Session],
    settings: Settings,
    request: AskRequest,
    snapshot: LibrarySourceSnapshot,
    segment_ids: list[int],
) -> list[EvidenceSegment]:
    with factory.begin() as db:
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        return read_evidence_in_session(db, settings, request, snapshot, segment_ids)


def read_evidence_in_session(
    db: Session,
    settings: Settings,
    request: AskRequest,
    snapshot: LibrarySourceSnapshot,
    segment_ids: list[int],
) -> list[EvidenceSegment]:
    require_current_source(db, request, snapshot)
    books = {book.id: book for book in snapshot.books}
    require_evidence_size(db, settings, list(books), segment_ids, use_whole_book=request.use_whole_book)
    book_order = {book_id: index for index, book_id in enumerate(books)}
    rows = db.execute(
        select(
            BookSegment.id,
            BookSegment.book_id,
            BookSegment.section_id,
            BookSegment.sequence,
            BookSegment.text_content,
        )
        .where(
            BookSegment.book_id.in_(books),
            true() if request.use_whole_book else BookSegment.id.in_(segment_ids),
        )
        .order_by(BookSegment.book_id, BookSegment.sequence)
    ).all()
    if not set(segment_ids).issubset({row.id for row in rows}):
        raise SourceChangedError
    sections = {}
    parents = {row.section_id for row in rows if row.section_id is not None}
    while parents:
        found = db.execute(
            select(BookSection.id, BookSection.parent_id, BookSection.title).where(
                BookSection.id.in_(parents),
                BookSection.book_id.in_(books),
            )
        ).all()
        sections.update({row.id: row for row in found})
        parents = {row.parent_id for row in found if row.parent_id is not None and row.parent_id not in sections}
    evidence = []
    for row in rows:
        book = books[row.book_id]
        version = book.content_version
        if version is None:
            raise SourceChangedError
        path = []
        section_id = row.section_id
        while section_id is not None:
            section = sections[section_id]
            path.append(section.title)
            section_id = section.parent_id
        evidence.append(
            EvidenceSegment(
                segment_id=row.id,
                book_id=book.id,
                content_version=version,
                section_id=row.section_id,
                sequence=row.sequence,
                text=row.text_content,
                path=[*([book.series_title] if book.series_title else []), book.title, *reversed(path)],
            )
        )
    order = {identity: index for index, identity in enumerate(segment_ids)}
    return sorted(
        evidence,
        key=lambda segment: (
            order.get(segment.segment_id, len(order)),
            book_order[segment.book_id],
            segment.sequence,
        ),
    )


def is_source_current(factory: sessionmaker[Session], request: AskRequest, snapshot: LibrarySourceSnapshot) -> bool:
    with factory.begin() as db:
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        try:
            require_current_source(db, request, snapshot)
            return True
        except SourceChangedError:
            return False
