"""Admit source-matched runs, generate outside transactions, then publish atomically."""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from google.genai import errors
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from voice_archive_api.core.config import Settings
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.summary import SummaryRun
from voice_archive_api.logic.summaries import (
    POLICY,
    SUMMARY_INSTRUCTIONS,
    BackgroundSummary,
    PreviousBookSummary,
    SourceFragment,
    SummaryBatchDraft,
    SummaryDraft,
    build_summary_scopes,
    build_summary_targets,
    compute_summary_source_fingerprint,
    compute_summary_target_end_indexes,
    format_summary_context,
    order_source_fragments,
    split_summary_targets,
    validate_summary_coverage,
)
from voice_archive_api.services.books.service import BookNotFound, get_book, lock_book_for_series_write
from voice_archive_api.services.library.provider import BookTooLargeError, generate_structured_output
from voice_archive_api.services.summary.cache import create_summary_cache

logger = logging.getLogger(__name__)
RUN_TIMEOUT = timedelta(hours=4)


@dataclass
class SummarySnapshot:
    book_id: int
    run_id: UUID
    source_fingerprint: str
    metadata: dict[str, str | None]
    fragments: list[SourceFragment]
    membership: tuple[int | None, int | None]
    previous_books: list[PreviousBookSummary]


def lock_summary_book(db: Session, book_id: int) -> Book:
    book = lock_book_for_series_write(db, book_id, {})
    if book is None:
        raise BookNotFound
    return book


def read_previous_book_summaries(db: Session, book: Book) -> list[PreviousBookSummary]:
    if book.series_id is None:
        return []
    assert book.series_index is not None
    return [
        PreviousBookSummary(
            id=row.id,
            position=row.series_index,
            title=row.title,
            content_version=row.content_version,
            summary=row.summary,
        )
        for row in db.scalars(
            select(Book)
            .where(Book.series_id == book.series_id, Book.series_index < book.series_index)
            .order_by(Book.series_index)
            .with_for_update(read=True)
        )
    ]


def read_summary_source(db: Session, book: Book, run_id: UUID) -> SummarySnapshot:
    sections = [
        SourceFragment(id=row.id, parent_id=row.parent_id, kind="section", text=row.title)
        for row in db.scalars(select(BookSection).where(BookSection.book_id == book.id).order_by(BookSection.sequence))
    ]
    passages = [
        SourceFragment(id=row.id, parent_id=row.section_id, kind="passage", text=row.text_content)
        for row in db.scalars(select(BookSegment).where(BookSegment.book_id == book.id).order_by(BookSegment.sequence))
    ]
    metadata = {"title": book.title, "author": book.author, "language": book.language}
    membership = (book.series_id, book.series_index)
    previous_books = read_previous_book_summaries(db, book)
    fragments = order_source_fragments(sections, passages)
    fingerprint = compute_summary_source_fingerprint(metadata, fragments, membership, previous_books)
    return SummarySnapshot(book.id, run_id, fingerprint, metadata, fragments, membership, previous_books)


def start_summary_run(factory: sessionmaker[Session], book_id: int, model: str) -> SummarySnapshot | UUID:
    with factory.begin() as db:
        book = lock_summary_book(db, book_id)
        if book.content_version is None:
            raise ValueError("Import canonical book text before generating summaries")
        now = datetime.now(UTC)
        running = db.scalars(
            select(SummaryRun).where(SummaryRun.book_id == book_id, SummaryRun.state == "running")
        ).all()
        if any(run.started_at > now - RUN_TIMEOUT for run in running):
            return book.content_version
        for run in running:
            run.state, run.finished_at, run.failure_reason = "failed", now, "Run exceeded four hours"
        snapshot = read_summary_source(db, book, uuid4())
        completed = db.scalar(
            select(SummaryRun)
            .where(
                SummaryRun.book_id == book_id,
                SummaryRun.state == "completed",
            )
            .order_by(SummaryRun.started_at.desc())
            .limit(1)
        )
        if (
            book.summary is not None
            and completed is not None
            and completed.source_fingerprint == snapshot.source_fingerprint
            and completed.model == model
            and completed.policy == POLICY
        ):
            return book.content_version
        db.add(
            SummaryRun(
                id=snapshot.run_id,
                book_id=book_id,
                source_fingerprint=snapshot.source_fingerprint,
                state="running",
                model=model,
                policy=POLICY,
                source_language=book.language,
                started_at=now,
            )
        )
        return snapshot


def fail_summary_run(factory: sessionmaker[Session], run_id: UUID) -> None:
    try:
        with factory.begin() as db:
            db.execute(
                update(SummaryRun)
                .where(SummaryRun.id == run_id, SummaryRun.state == "running")
                .values(
                    state="failed",
                    finished_at=datetime.now(UTC),
                    failure_reason="Summary generation or publication failed",
                )
            )
    except SQLAlchemyError:
        logger.warning("Unable to record summary run failure; a later invocation can recover after four hours")


def ensure_summary_run_is_active(factory: sessionmaker[Session], run_id: UUID) -> None:
    with factory() as db:
        if db.scalar(select(SummaryRun.state).where(SummaryRun.id == run_id)) != "running":
            raise ValueError("Summary run changed; stop generation")


def publish_book_summaries(
    factory: sessionmaker[Session], snapshot: SummarySnapshot, drafts: list[SummaryDraft]
) -> UUID:
    validate_summary_coverage(build_summary_targets(snapshot.book_id, snapshot.fragments), drafts)
    with factory.begin() as db:
        book = lock_summary_book(db, snapshot.book_id)
        run = db.get(SummaryRun, snapshot.run_id, with_for_update=True)
        if (
            book.content_version is None
            or run is None
            or run.state != "running"
            or run.book_id != snapshot.book_id
            or run.source_fingerprint != snapshot.source_fingerprint
            or read_summary_source(db, book, snapshot.run_id).source_fingerprint != snapshot.source_fingerprint
        ):
            raise ValueError("Book source, background or summary run changed; regenerate summaries")
        for kind, table in (("section", BookSection), ("segment", BookSegment)):
            rows = [{"id": draft.id, "summary": draft.summary} for draft in drafts if draft.kind == kind]
            if rows:
                db.execute(update(table), rows)
        book.summary = next(draft.summary for draft in drafts if draft.kind == "book")
        book.content_version = uuid4()
        book.updated_at = datetime.now(UTC)
        run.state, run.finished_at = "completed", book.updated_at
        return book.content_version


def clear_book_summaries(factory: sessionmaker[Session], book_id: int) -> UUID | None:
    with factory.begin() as db:
        book = get_book(db, book_id, for_update=True)
        changed = book.summary is not None
        for table in (BookSection, BookSegment):
            cleared = db.scalar(
                update(table)
                .where(table.book_id == book_id, table.summary.is_not(None))
                .values(summary=None)
                .returning(table.id)
            )
            changed = cleared is not None or changed
        db.execute(
            update(SummaryRun)
            .where(SummaryRun.book_id == book_id, SummaryRun.state == "running")
            .values(
                state="failed",
                finished_at=datetime.now(UTC),
                failure_reason="Summaries cleared",
            )
        )
        book.summary = None
        if changed:
            book.content_version = uuid4()
            book.updated_at = datetime.now(UTC)
        return book.content_version


async def generate_book_summaries(
    factory: sessionmaker[Session],
    settings: Settings,
    book_id: int,
    *,
    retries: int = 1,
    report_progress: Callable[[str], None] = lambda message: None,
) -> UUID:
    if not 0 <= retries <= 2:
        raise ValueError("Use zero to two retries per batch")
    prepared = await run_in_threadpool(start_summary_run, factory, book_id, settings.gemini_text_model)
    if isinstance(prepared, UUID):
        report_progress("Generation skipped: a run is active or summaries already match the source and policy")
        return prepared
    try:
        return await generate_and_publish_summaries(factory, settings, prepared, retries, report_progress)
    except BaseException:
        await run_in_threadpool(fail_summary_run, factory, prepared.run_id)
        raise


async def generate_and_publish_summaries(
    factory: sessionmaker[Session],
    settings: Settings,
    snapshot: SummarySnapshot,
    retries: int,
    report_progress: Callable[[str], None],
) -> UUID:
    scopes = build_summary_scopes(snapshot.book_id, snapshot.fragments)
    drafts: list[SummaryDraft] = []
    previous_roots: list[BackgroundSummary] = []
    previous_scope = None
    for index, scope in enumerate(scopes, start=1):
        await run_in_threadpool(ensure_summary_run_is_active, factory, snapshot.run_id)
        source = format_summary_context(
            scope,
            snapshot.metadata,
            snapshot.previous_books,
            previous_roots if scope.target.kind == "section" else [],
            previous_scope if scope.target.kind == "section" else None,
        )
        end_indexes = compute_summary_target_end_indexes(scope)
        ordered_targets = sorted(
            scope.targets,
            key=lambda target: (end_indexes[(target.kind, target.id)], target.kind != "segment"),
        )
        batches = split_summary_targets(ordered_targets, scope.fragments)
        async with create_summary_cache(settings, SUMMARY_INSTRUCTIONS, source, enabled=len(batches) > 1) as cache:
            for batch_index, targets in enumerate(batches, start=1):
                target_text = json.dumps(
                    {
                        "TARGETS": [
                            {**target.model_dump(), "end_fragment_index": end_indexes[(target.kind, target.id)]}
                            for target in targets
                        ]
                    }
                )
                for attempt in range(retries + 1):
                    await run_in_threadpool(ensure_summary_run_is_active, factory, snapshot.run_id)
                    report_progress(
                        f"{index}/{len(scopes)} {scope.target.kind} {scope.target.id}, "
                        f"batch {batch_index}/{len(batches)} ({len(targets)} targets), attempt {attempt + 1}"
                    )
                    try:
                        cached_name = await cache.get_available_name() if cache else None
                        result = await generate_structured_output(
                            settings,
                            SUMMARY_INSTRUCTIONS,
                            [target_text] if cached_name else [source, target_text],
                            SummaryBatchDraft,
                            api_key=settings.effective_gemini_batch_api_key,
                            max_output_tokens=None,
                            cached_content=cached_name,
                        )
                        validate_summary_coverage(targets, result.summaries)
                        drafts.extend(result.summaries)
                        break
                    except BookTooLargeError as exc:
                        raise ValueError(
                            f"{scope.target.kind} {scope.target.id}: complete canonical context exceeds the model input limit"
                        ) from exc
                    except (errors.APIError, httpx.HTTPError, TimeoutError, ValueError) as exc:
                        # A cache must not make a generation retry depend on the
                        # same unavailable/expired provider resource.
                        cache = None
                        if attempt == retries:
                            raise ValueError(
                                f"{scope.target.kind} {scope.target.id}: generation failed; check provider configuration/availability and retry"
                            ) from exc
        if scope.target.kind == "section":
            previous_roots.append(
                BackgroundSummary(
                    title=next(f.text for f in scope.fragments if f.kind == "section" and f.id == scope.target.id),
                    summary=next(
                        draft.summary for draft in drafts if draft.kind == "section" and draft.id == scope.target.id
                    ),
                )
            )
            previous_scope = scope
    return await run_in_threadpool(publish_book_summaries, factory, snapshot, drafts)
