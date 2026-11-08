"""Admit source-matched runs, generate outside transactions, then publish atomically."""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import httpx
from google.genai import errors, types
from sqlalchemy import delete, or_, select, true, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.elements import ColumnElement
from starlette.concurrency import run_in_threadpool

from voice_archive_api.core.config import Settings
from voice_archive_api.core.logging import log_exception
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.summary import SummaryBatchCheckpoint, SummaryGenerationRun
from voice_archive_api.logic.books.validation import resolve_primary_language
from voice_archive_api.logic.summaries import (
    POLICY,
    SUMMARY_INSTRUCTIONS,
    TRANSLATION_INSTRUCTIONS,
    BookContentFragment,
    BookSummary,
    BookSummaryInput,
    GeneratedSummaryWithTranslation,
    RootSectionSummaryInput,
    SectionSummary,
    SummaryBatchResult,
    SummaryOnlyBatchResult,
    SummaryTarget,
    build_book_summary_input,
    build_content_summary_targets,
    build_root_section_summary_inputs,
    build_summary_targets,
    compute_summary_input_fingerprint,
    compute_summary_target_end_indexes,
    format_summary_background_text,
    format_summary_context,
    order_source_fragments,
    split_summary_targets,
    validate_summary_coverage,
    validate_summary_translations,
)
from voice_archive_api.services.books.service import BookNotFound, get_book, lock_book_for_series_write
from voice_archive_api.services.library.provider import BookTooLargeError, generate_structured_output
from voice_archive_api.services.summary.cache import create_summary_context_cache

logger = logging.getLogger(__name__)
RUN_TIMEOUT = timedelta(hours=4)


@dataclass
class SummaryGenerationInput:
    """Source, context and run metadata captured before generation begins."""

    book_id: int
    run_id: UUID
    input_fingerprint: str
    book_metadata: dict[str, str | None]
    primary_language: str
    source_fragments: list[BookContentFragment]
    series_membership: tuple[int | None, int | None]
    earlier_book_summaries: list[BookSummary]
    root_section_id: int | None = None
    allow_stale_downstream: bool = False
    earlier_root_summaries: list[SectionSummary] = field(default_factory=list)
    previous_root_text: str | None = None
    downstream_blockers: list[str] = field(default_factory=list)

    def build_targets(self) -> list[SummaryTarget]:
        if self.root_section_id is None:
            return build_summary_targets(self.book_id, self.source_fragments)
        return build_content_summary_targets(self.source_fragments)

    @property
    def is_passage_translation_required(self) -> bool:
        return self.primary_language != "en"


def lock_summary_book(db: Session, book_id: int) -> Book:
    book = lock_book_for_series_write(db, book_id, {})
    if book is None:
        raise BookNotFound
    return book


def read_earlier_book_summaries(db: Session, book: Book) -> list[BookSummary]:
    if book.series_id is None:
        return []
    assert book.series_index is not None
    return [
        BookSummary(
            id=row.id,
            title=row.title,
            summary=row.summary,
        )
        for row in db.scalars(
            select(Book)
            .where(Book.series_id == book.series_id, Book.series_index < book.series_index)
            .order_by(Book.series_index)
            .with_for_update(read=True)
        )
    ]


def read_summary_source(
    db: Session, book: Book, run_id: UUID, root_section_id: int | None = None, allow_stale_downstream: bool = False
) -> SummaryGenerationInput:
    primary_language = resolve_primary_language(book.language)
    section_rows = db.scalars(
        select(BookSection).where(BookSection.book_id == book.id).order_by(BookSection.sequence)
    ).all()
    passage_rows = db.scalars(
        select(BookSegment).where(BookSegment.book_id == book.id).order_by(BookSegment.sequence)
    ).all()
    sections = [
        BookContentFragment(id=row.id, parent_id=row.parent_id, kind="section", text=row.title) for row in section_rows
    ]
    passages = [
        BookContentFragment(id=row.id, parent_id=row.section_id, kind="passage", text=row.text_content)
        for row in passage_rows
    ]
    book_metadata = {"title": book.title, "author": book.author, "language": book.language}
    series_membership = (book.series_id, book.series_index)
    earlier_book_summaries = read_earlier_book_summaries(db, book)
    source_fragments = order_source_fragments(sections, passages)
    blockers: list[str] = []

    # Stays empty if you're running the summary for the whole book
    earlier_root_summaries: list[SectionSummary] = []
    previous_root_text: str | None = None

    if root_section_id is not None:
        root_section_inputs = build_root_section_summary_inputs(source_fragments)

        selected_root_index = next(
            # Find the input for the requested root section.
            (
                root_section_index
                for root_section_index, root_section_input in enumerate(root_section_inputs)
                if root_section_input.root_section_id == root_section_id
            ),
            None,
        )
        if selected_root_index is None:
            raise ValueError("Root section must belong to the selected book and have no parent")

        summaries = {row.id: row.summary for row in section_rows}
        titles = {row.id: row.title for row in section_rows}
        earlier_root_summaries = [
            SectionSummary(
                id=root_section_input.root_section_id,
                title=titles[root_section_input.root_section_id],
                summary=summaries[root_section_input.root_section_id],
            )
            for root_section_input in root_section_inputs[:selected_root_index]
        ]

        missing = [str(root.id) for root in earlier_root_summaries if not (root.summary or "").strip()]
        # Retain IDs for actionable prerequisite errors without exposing them in prompts.
        if missing:
            raise ValueError(f"Missing earlier root summaries: {', '.join(missing)}")

        previous_root_text = (
            format_summary_background_text(root_section_inputs[selected_root_index - 1])
            if selected_root_index
            else None
        )

        summarized = {
            *(("section", row.id) for row in section_rows if row.summary is not None),
            *(("passage", row.id) for row in passage_rows if row.summary is not None),
        }

        blockers = [
            f"root section {root_section_input.root_section_id}"
            for root_section_input in root_section_inputs[selected_root_index + 1 :]
            if any((fragment.kind, fragment.id) in summarized for fragment in root_section_input.source_fragments)
        ]

        # You only need source fragments for the selected root on this job
        source_fragments = root_section_inputs[selected_root_index].source_fragments

    if book.series_id is not None:
        later_books = db.scalars(
            select(Book)
            .where(
                Book.series_id == book.series_id,
                Book.series_index > book.series_index,
                or_(
                    Book.summary.is_not(None),
                    select(BookSection.id)
                    .where(BookSection.book_id == Book.id, BookSection.summary.is_not(None))
                    .exists(),
                    select(BookSegment.id)
                    .where(BookSegment.book_id == Book.id, BookSegment.summary.is_not(None))
                    .exists(),
                ),
            )
            .order_by(Book.series_index)
        )
        blockers.extend(f"book {row.id}" for row in later_books)

    fingerprint = compute_summary_input_fingerprint(
        book_metadata, source_fragments, series_membership, earlier_book_summaries
    )

    if root_section_id is not None:
        fingerprint = sha256(
            json.dumps(
                [
                    # If the summary for the whole book changed, then the summary for a single chapter should also change
                    fingerprint,
                    root_section_id,
                    [root.model_dump() for root in earlier_root_summaries],
                    previous_root_text,
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()

    return SummaryGenerationInput(
        book.id,
        run_id,
        fingerprint,
        book_metadata,
        primary_language,
        source_fragments,
        series_membership,
        earlier_book_summaries,
        root_section_id,
        allow_stale_downstream,
        earlier_root_summaries,
        previous_root_text,
        blockers,
    )


def validate_summary_dependencies(generation_input: SummaryGenerationInput) -> None:
    missing = [str(book.id) for book in generation_input.earlier_book_summaries if not (book.summary or "").strip()]
    if missing:
        raise ValueError(f"Missing earlier book summaries: {', '.join(missing)}")
    if generation_input.downstream_blockers and not generation_input.allow_stale_downstream:
        raise ValueError(
            f"Later summaries exist: {', '.join(generation_input.downstream_blockers)}. "
            "Use --allow-stale-downstream to accept potential staleness."
        )


def start_summary_run(
    factory: sessionmaker[Session],
    book_id: int,
    model: str,
    *,
    root_section_id: int | None = None,
    allow_stale_downstream: bool = False,
    effort: str = "low",
) -> SummaryGenerationInput | UUID:
    if effort not in {"low", "medium", "high"}:
        raise ValueError("Use low, medium or high reasoning effort")

    # Existing policies without an effort suffix represent low-effort generation.
    policy = POLICY if effort == "low" else f"{POLICY};effort={effort}"

    with factory.begin() as db:
        book = lock_summary_book(db, book_id)

        if book.content_version is None:
            raise ValueError("Import canonical book text before generating summaries")

        generation_input = read_summary_source(db, book, uuid4(), root_section_id, allow_stale_downstream)
        validate_summary_dependencies(generation_input)
        now = datetime.now(UTC)
        running = db.scalars(
            select(SummaryGenerationRun).where(
                SummaryGenerationRun.book_id == book_id, SummaryGenerationRun.state == "running"
            )
        ).all()

        if any(run.started_at > now - RUN_TIMEOUT for run in running):
            return book.content_version

        for run in running:
            run.state, run.finished_at, run.failure_reason = "failed", now, "Run exceeded four hours"

        completed = db.scalar(
            select(SummaryGenerationRun)
            .where(
                SummaryGenerationRun.book_id == book_id,
                SummaryGenerationRun.state == "completed",
                or_(
                    SummaryGenerationRun.root_section_id == root_section_id,
                    SummaryGenerationRun.root_section_id.is_(None),
                )
                if root_section_id is not None
                else true(),
            )
            .order_by(SummaryGenerationRun.started_at.desc())
            .limit(1)
        )
        if (
            (
                book.summary
                if root_section_id is None
                else db.scalar(select(BookSection.summary).where(BookSection.id == root_section_id))
            )
            is not None
            and completed is not None
            and completed.root_section_id == root_section_id
            and completed.input_fingerprint == generation_input.input_fingerprint
            and completed.model == model
            and completed.policy == policy
        ):
            return book.content_version
        db.add(
            SummaryGenerationRun(
                id=generation_input.run_id,
                book_id=book_id,
                root_section_id=root_section_id,
                allow_stale_downstream=allow_stale_downstream,
                input_fingerprint=generation_input.input_fingerprint,
                state="running",
                model=model,
                policy=policy,
                source_language=book.language,
                started_at=now,
            )
        )
        return generation_input


def record_summary_token_usage(
    factory: sessionmaker[Session], run_id: UUID, usage: types.GenerateContentResponseUsageMetadata | None
) -> None:
    metadata = usage or types.GenerateContentResponseUsageMetadata()
    is_missing = metadata.prompt_token_count is None or metadata.total_token_count is None
    # Commit each response before validation/publication so failed attempts are retained.
    # Do not filter by state: a late response still consumed tokens if the run was cleared.
    with factory.begin() as db:
        db.execute(
            update(SummaryGenerationRun)
            .where(SummaryGenerationRun.id == run_id)
            .values(
                input_tokens=SummaryGenerationRun.input_tokens + (metadata.prompt_token_count or 0),
                cached_input_tokens=SummaryGenerationRun.cached_input_tokens
                + (metadata.cached_content_token_count or 0),
                output_tokens=SummaryGenerationRun.output_tokens + (metadata.candidates_token_count or 0),
                thinking_tokens=SummaryGenerationRun.thinking_tokens + (metadata.thoughts_token_count or 0),
                total_tokens=SummaryGenerationRun.total_tokens + (metadata.total_token_count or 0),
                generation_response_count=SummaryGenerationRun.generation_response_count + 1,
                missing_usage_response_count=SummaryGenerationRun.missing_usage_response_count + int(is_missing),
            )
        )


def fail_summary_run(factory: sessionmaker[Session], run_id: UUID) -> None:
    try:
        with factory.begin() as db:
            db.execute(
                update(SummaryGenerationRun)
                .where(SummaryGenerationRun.id == run_id, SummaryGenerationRun.state == "running")
                .values(
                    state="failed",
                    finished_at=datetime.now(UTC),
                    failure_reason="Summary generation or publication failed",
                )
            )
    except SQLAlchemyError as exc:
        log_exception(
            logger,
            f"Unable to record summary run failure: run={run_id}; recover after four hours",
            exc,
            level=logging.WARNING,
        )


def ensure_summary_run_is_active(factory: sessionmaker[Session], run_id: UUID) -> None:
    with factory() as db:
        if db.scalar(select(SummaryGenerationRun.state).where(SummaryGenerationRun.id == run_id)) != "running":
            raise ValueError("Summary run changed; stop generation")


def read_summary_batch_checkpoints(factory: sessionmaker[Session], run_id: UUID) -> dict[str, SummaryBatchResult]:
    with factory() as db:
        run = db.get(SummaryGenerationRun, run_id)
        if run is None or run.state != "running":
            raise ValueError("Summary run changed; stop generation")

        checkpoints = db.scalars(
            select(SummaryBatchCheckpoint)
            .join(SummaryGenerationRun)
            .where(
                SummaryGenerationRun.book_id == run.book_id,
                SummaryGenerationRun.root_section_id == run.root_section_id,
                SummaryGenerationRun.input_fingerprint == run.input_fingerprint,
                SummaryGenerationRun.model == run.model,
                SummaryGenerationRun.policy == run.policy,
                SummaryGenerationRun.allow_stale_downstream == run.allow_stale_downstream,
                or_(SummaryGenerationRun.id == run_id, SummaryGenerationRun.state == "failed"),
            )
            .order_by(SummaryGenerationRun.started_at.desc())
        )
        results: dict[str, SummaryBatchResult] = {}
        for checkpoint in checkpoints:
            if checkpoint.request_fingerprint not in results:
                results[checkpoint.request_fingerprint] = SummaryBatchResult.model_validate(
                    {"summaries": checkpoint.summaries}
                )
        return results


def save_summary_batch_checkpoint(
    factory: sessionmaker[Session],
    run_id: UUID,
    request_fingerprint: str,
    source_target: SummaryTarget,
    batch_index: int,
    result: SummaryBatchResult,
) -> None:
    with factory.begin() as db:
        run = db.get(SummaryGenerationRun, run_id, with_for_update=True)
        if run is None or run.state != "running":
            raise ValueError("Summary run changed; stop generation")
        db.add(
            SummaryBatchCheckpoint(
                run_id=run_id,
                request_fingerprint=request_fingerprint,
                target_kind=source_target.kind,
                target_id=source_target.id,
                batch_index=batch_index,
                summaries=[summary.model_dump() for summary in result.summaries],
            )
        )


def publish_book_summaries(
    factory: sessionmaker[Session],
    generation_input: SummaryGenerationInput,
    drafts: list[GeneratedSummaryWithTranslation],
) -> UUID:
    validate_summary_coverage(generation_input.build_targets(), drafts)
    validate_summary_translations(drafts, translate_passages=generation_input.is_passage_translation_required)
    with factory.begin() as db:
        book = lock_summary_book(db, generation_input.book_id)
        run = db.get(SummaryGenerationRun, generation_input.run_id, with_for_update=True)
        if (
            book.content_version is None
            or run is None
            or run.state != "running"
            or run.book_id != generation_input.book_id
            or run.root_section_id != generation_input.root_section_id
            or run.allow_stale_downstream != generation_input.allow_stale_downstream
            or run.input_fingerprint != generation_input.input_fingerprint
        ):
            raise ValueError("Book source, background or summary run changed; regenerate summaries")
        current = read_summary_source(
            db, book, generation_input.run_id, generation_input.root_section_id, generation_input.allow_stale_downstream
        )
        if current.input_fingerprint != generation_input.input_fingerprint:
            raise ValueError("Book source or background changed; regenerate summaries")
        validate_summary_dependencies(current)
        for kind, table in (("section", BookSection), ("segment", BookSegment)):
            rows = [
                {
                    "id": draft.id,
                    "summary": draft.summary,
                    **({"english_translation": draft.english_translation} if kind == "segment" else {}),
                }
                for draft in drafts
                if draft.kind == kind
            ]
            if rows:
                db.execute(update(table), rows)
        if generation_input.root_section_id is None:
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
            has_output: ColumnElement[bool] = table.summary.is_not(None)
            values: dict[str, None] = {"summary": None}
            if table is BookSegment:
                has_output = has_output | BookSegment.english_translation.is_not(None)
                values["english_translation"] = None
            cleared = db.scalar(
                update(table).where(table.book_id == book_id, has_output).values(**values).returning(table.id)
            )
            changed = cleared is not None or changed
        db.execute(
            update(SummaryGenerationRun)
            .where(SummaryGenerationRun.book_id == book_id, SummaryGenerationRun.state == "running")
            .values(
                state="failed",
                finished_at=datetime.now(UTC),
                failure_reason="Summaries cleared",
            )
        )
        db.execute(
            delete(SummaryBatchCheckpoint).where(
                SummaryBatchCheckpoint.run_id.in_(
                    select(SummaryGenerationRun.id).where(SummaryGenerationRun.book_id == book_id)
                )
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
    effort: str = "low",
    root_section_id: int | None = None,
    allow_stale_downstream: bool = False,
    report_progress: Callable[[str], None] = lambda message: None,
) -> UUID:
    if not 0 <= retries <= 2:
        raise ValueError("Use zero to two retries per batch")
    prepared = await run_in_threadpool(
        start_summary_run,
        factory,
        book_id,
        settings.gemini_text_model,
        root_section_id=root_section_id,
        allow_stale_downstream=allow_stale_downstream,
        effort=effort,
    )
    if isinstance(prepared, UUID):
        report_progress("Generation skipped: a run is active or summaries already match the source and policy")
        return prepared
    try:
        if prepared.downstream_blockers:
            report_progress(
                "Allowing potentially stale downstream summaries: " + ", ".join(prepared.downstream_blockers)
            )
        return await generate_and_publish_summaries(
            factory, settings, prepared, retries, report_progress, effort=effort
        )
    except BaseException:
        await run_in_threadpool(fail_summary_run, factory, prepared.run_id)
        raise


async def generate_and_publish_summaries(
    factory: sessionmaker[Session],
    settings: Settings,
    generation_input: SummaryGenerationInput,
    retries: int,
    report_progress: Callable[[str], None],
    *,
    effort: str = "low",
) -> UUID:
    summary_inputs: list[RootSectionSummaryInput | BookSummaryInput] = [
        *build_root_section_summary_inputs(generation_input.source_fragments)
    ]
    if generation_input.root_section_id is None:
        summary_inputs.append(build_book_summary_input(generation_input.book_id, generation_input.source_fragments))
    checkpoints = await run_in_threadpool(read_summary_batch_checkpoints, factory, generation_input.run_id)
    drafts: list[GeneratedSummaryWithTranslation] = []
    instructions = SUMMARY_INSTRUCTIONS
    output_type: type[SummaryBatchResult] | type[SummaryOnlyBatchResult] = SummaryOnlyBatchResult
    if generation_input.is_passage_translation_required:
        instructions += TRANSLATION_INSTRUCTIONS
        output_type = SummaryBatchResult

    async def record_usage(usage: types.GenerateContentResponseUsageMetadata | None) -> None:
        await run_in_threadpool(record_summary_token_usage, factory, generation_input.run_id, usage)

    earlier_root_summaries = list(generation_input.earlier_root_summaries)
    previous_root_text = generation_input.previous_root_text
    for index, summary_input in enumerate(summary_inputs, start=1):
        await run_in_threadpool(ensure_summary_run_is_active, factory, generation_input.run_id)
        source = format_summary_context(
            summary_input,
            {**generation_input.book_metadata, "language": generation_input.primary_language},
            generation_input.earlier_book_summaries,
            earlier_root_summaries if isinstance(summary_input, RootSectionSummaryInput) else [],
            previous_root_text if isinstance(summary_input, RootSectionSummaryInput) else None,
        )
        end_indexes = compute_summary_target_end_indexes(summary_input)
        ordered_targets = sorted(
            summary_input.targets,
            key=lambda target: (end_indexes[(target.kind, target.id)], target.kind != "segment"),
        )
        batches = split_summary_targets(
            ordered_targets,
            summary_input.source_fragments,
            translate_passages=generation_input.is_passage_translation_required,
        )
        batch_requests = []
        for targets in batches:
            target_text = json.dumps(
                {
                    "TARGETS": [
                        {**target.model_dump(), "end_fragment_index": end_indexes[(target.kind, target.id)]}
                        for target in targets
                    ]
                }
            )
            request_fingerprint = sha256(
                json.dumps(
                    [
                        instructions,
                        output_type.model_json_schema(),
                        settings.gemini_text_model,
                        effort,
                        source,
                        target_text,
                    ]
                ).encode()
            ).hexdigest()
            batch_requests.append((targets, target_text, request_fingerprint))
        pending_batches = sum(fingerprint not in checkpoints for _, _, fingerprint in batch_requests)
        async with create_summary_context_cache(settings, instructions, source, enabled=pending_batches > 1) as cache:
            for batch_index, (targets, target_text, request_fingerprint) in enumerate(batch_requests, start=1):
                if saved_result := checkpoints.get(request_fingerprint):
                    await run_in_threadpool(ensure_summary_run_is_active, factory, generation_input.run_id)
                    validate_summary_coverage(targets, saved_result.summaries)
                    validate_summary_translations(
                        saved_result.summaries, translate_passages=generation_input.is_passage_translation_required
                    )
                    report_progress(
                        f"{index}/{len(summary_inputs)} {summary_input.source_target.kind} {summary_input.source_target.id}, "
                        f"batch {batch_index}/{len(batches)} reused checkpoint"
                    )
                    drafts.extend(saved_result.summaries)
                    continue
                for attempt in range(retries + 1):
                    await run_in_threadpool(ensure_summary_run_is_active, factory, generation_input.run_id)
                    report_progress(
                        f"{index}/{len(summary_inputs)} {summary_input.source_target.kind} {summary_input.source_target.id}, "
                        f"batch {batch_index}/{len(batches)} ({len(targets)} targets), attempt {attempt + 1}"
                    )
                    try:
                        provider_cache_name = await cache.get_available_name() if cache else None
                        generated_result = await generate_structured_output(
                            settings,
                            instructions,
                            [target_text] if provider_cache_name else [source, target_text],
                            output_type,
                            effort=effort,
                            api_key=settings.effective_gemini_batch_api_key,
                            max_output_tokens=None,
                            cached_content=provider_cache_name,
                            report_usage=record_usage,
                        )
                        result = SummaryBatchResult.model_validate(
                            generated_result.model_dump()
                        )  # For English only, the translation is automatically set to None
                        validate_summary_coverage(targets, result.summaries)
                        validate_summary_translations(
                            result.summaries, translate_passages=generation_input.is_passage_translation_required
                        )
                        await run_in_threadpool(
                            save_summary_batch_checkpoint,
                            factory,
                            generation_input.run_id,
                            request_fingerprint,
                            summary_input.source_target,
                            batch_index,
                            result,
                        )
                        drafts.extend(result.summaries)
                        break
                    except BookTooLargeError as exc:
                        log_exception(
                            logger,
                            f"Summary input exceeds model limit: run={generation_input.run_id} book={generation_input.book_id} "
                            f"target={summary_input.source_target.kind}:{summary_input.source_target.id} batch={batch_index}",
                            exc,
                            settings=settings,
                        )
                        raise ValueError(
                            f"{summary_input.source_target.kind} {summary_input.source_target.id}: complete canonical context exceeds the model input limit"
                        ) from exc
                    except (errors.APIError, httpx.HTTPError, TimeoutError, ValueError) as exc:
                        log_exception(
                            logger,
                            f"Summary generation attempt failed: run={generation_input.run_id} book={generation_input.book_id} "
                            f"target={summary_input.source_target.kind}:{summary_input.source_target.id} "
                            f"batch={batch_index}/{len(batches)} attempt={attempt + 1}/{retries + 1}",
                            exc,
                            settings=settings,
                        )
                        # A cache must not make a generation retry depend on the
                        # same unavailable/expired provider resource.
                        cache = None
                        if attempt == retries:
                            raise ValueError(
                                f"{summary_input.source_target.kind} {summary_input.source_target.id}: generation failed; see the logged exception for details"
                            ) from exc
        if isinstance(summary_input, RootSectionSummaryInput):
            earlier_root_summaries.append(
                SectionSummary(
                    id=summary_input.source_target.id,
                    title=next(
                        f.text
                        for f in summary_input.source_fragments
                        if f.kind == "section" and f.id == summary_input.source_target.id
                    ),
                    summary=next(
                        draft.summary
                        for draft in drafts
                        if draft.kind == "section" and draft.id == summary_input.source_target.id
                    ),
                )
            )
            previous_root_text = format_summary_background_text(summary_input)
    return await run_in_threadpool(publish_book_summaries, factory, generation_input, drafts)
