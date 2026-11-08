"""Claim missing window vectors, then submit asynchronous embedding batches."""

import io
import json
import logging
from collections.abc import Callable
from uuid import UUID, uuid4

from google import genai
from google.genai import errors, types
from google.genai.local_tokenizer import LocalTokenizer
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from voice_archive_api.core.config import Settings
from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.core.logging import log_exception
from voice_archive_api.db.models.batch_queue import BookBatchJob
from voice_archive_api.db.models.embedding_batch import EmbeddingBatchJob, EmbeddingBatchRequest
from voice_archive_api.db.models.windows import RetrievalWindow, WindowEmbedding
from voice_archive_api.db.operations import create_wall_clock_expression
from voice_archive_api.logic.batches import BookBatchJobState, EmbeddingBatchState
from voice_archive_api.logic.windows import compute_embedding_input_hash, format_embedding_input
from voice_archive_api.services.batch_queue import (
    enqueue_book_batch_job,
    has_active_batch,
    lock_batch_queue,
    release_batch_queue_slot,
)
from voice_archive_api.services.books.service import BookNotFound, lock_book_for_series_write

logger = logging.getLogger(__name__)


class EmbeddingInputError(ValueError):
    def __init__(self, book_id: int, message: str) -> None:
        super().__init__(message)
        self.book_id = book_id


class EmbeddingSubmissionError(ValueError):
    """A submission failure whose original cause has already been logged."""


def enqueue_book_embeddings(factory: sessionmaker[Session], book_id: int) -> None:
    with factory.begin() as db:
        lock_batch_queue(db)

        if lock_book_for_series_write(db, book_id, {}) is None:
            raise BookNotFound

        if not db.scalar(select(RetrievalWindow.id).where(RetrievalWindow.book_id == book_id).limit(1)):
            raise ValueError("Build the book's windows before submitting embeddings")

        enqueue_book_batch_job(db, book_id, "embeddings")


def create_embedding_batch_request_input(
    factory: sessionmaker[Session], settings: Settings, count_tokens: Callable[[str], int]
) -> tuple[UUID, bytes, int, str, int] | None:
    with factory.begin() as db:
        lock_batch_queue(db)

        if has_active_batch(db) or has_outstanding_embedding_batch(db):
            return None

        jobs = db.scalars(
            select(BookBatchJob)
            .where(BookBatchJob.state == BookBatchJobState.QUEUED, BookBatchJob.kind == "embeddings")
            .order_by(BookBatchJob.requested_at, BookBatchJob.book_id)
        ).all()

        for job in jobs:
            try:
                book = lock_book_for_series_write(db, job.book_id, {})
            except ApplicationError as exc:
                job.state = BookBatchJobState.FAILED
                log_exception(logger, f"Embedding book unavailable: book={job.book_id}", exc, settings=settings)
                continue
            if book is None:
                job.state = BookBatchJobState.FAILED
                continue

            rows = find_eligible_embedding_inputs(db, job, settings.embedding_max_attempts)
            if not rows:
                finish_book_embedding_job(db, job)
                continue

            try:
                # Select only as many inputs from the eligible set as can fit in one batch request
                model, inputs = select_embedding_batch_inputs(
                    job.book_id, rows, count_tokens, settings.embedding_batch_tokens
                )
            except EmbeddingInputError as exc:
                job.state = BookBatchJobState.FAILED
                log_exception(logger, f"Invalid embedding input: book={job.book_id}", exc, settings=settings)
                continue

            return create_claimed_embedding_batch(db, job, model, inputs)

    return None


def has_outstanding_embedding_batch(db: Session) -> bool:
    return (
        db.scalar(
            select(EmbeddingBatchJob.id)
            .where(EmbeddingBatchJob.state.in_((EmbeddingBatchState.SUBMITTING, EmbeddingBatchState.SUBMITTED)))
            .limit(1)
        )
        is not None
    )


def find_eligible_embedding_inputs(
    db: Session, job: BookBatchJob, max_attempts: int
) -> list[tuple[WindowEmbedding, RetrievalWindow]]:
    attempts = (
        select(func.count(EmbeddingBatchRequest.id))
        .join(EmbeddingBatchJob)
        .where(
            EmbeddingBatchRequest.embedding_id == WindowEmbedding.id,
            EmbeddingBatchRequest.input_hash == WindowEmbedding.input_hash,
            EmbeddingBatchJob.created_at >= job.requested_at,
        )
        .scalar_subquery()
    )
    statement = (
        select(WindowEmbedding, RetrievalWindow)
        .join(RetrievalWindow)
        .where(RetrievalWindow.book_id == job.book_id, WindowEmbedding.vector.is_(None), attempts < max_attempts)
        .order_by(WindowEmbedding.model, RetrievalWindow.sequence, WindowEmbedding.id)
    )
    return [(embedding, window) for embedding, window in db.execute(statement)]


def finish_book_embedding_job(db: Session, job: BookBatchJob) -> None:
    missing = db.scalar(
        select(WindowEmbedding.id)
        .join(RetrievalWindow)
        .where(RetrievalWindow.book_id == job.book_id, WindowEmbedding.vector.is_(None))
        .limit(1)
    )
    job.state = BookBatchJobState.FAILED if missing else BookBatchJobState.COMPLETED
    if missing:
        logger.error("Embedding retries exhausted: book=%s", job.book_id)


def select_embedding_batch_inputs(
    book_id: int,
    eligible_embedding_inputs: list[tuple[WindowEmbedding, RetrievalWindow]],
    count_tokens: Callable[[str], int],
    token_budget: int,
) -> tuple[str, list[tuple[WindowEmbedding, str]]]:
    model = eligible_embedding_inputs[0][0].model
    selected = []
    total_tokens = 0
    for embedding, window in eligible_embedding_inputs:
        if embedding.model != model:
            break
        source = window.primary_text if embedding.kind == "primary" else window.translated_text
        if source is None or embedding.input_hash != compute_embedding_input_hash(
            window.path, source, model=model, dimensions=embedding.dimensions, input_format=embedding.input_format
        ):
            raise EmbeddingInputError(
                book_id, "Embedding input hash does not match stored window text; rebuild windows"
            )
        content = format_embedding_input(window.path, source)
        tokens = count_tokens(content) + 16
        if tokens > token_budget:
            raise EmbeddingInputError(book_id, f"Embedding input exceeds batch token budget: embedding={embedding.id}")
        if total_tokens + tokens > token_budget:
            break
        selected.append((embedding, content))
        total_tokens += tokens
    return model, selected


def create_claimed_embedding_batch(
    db: Session, job: BookBatchJob, model: str, inputs: list[tuple[WindowEmbedding, str]]
) -> tuple[UUID, bytes, int, str, int]:
    batch_id = uuid4()
    job.state = BookBatchJobState.RUNNING
    job.active_batch_id = batch_id
    db.add(
        EmbeddingBatchJob(
            id=batch_id,
            book_id=job.book_id,
            model=model,
            state=EmbeddingBatchState.SUBMITTING,
            created_at=create_wall_clock_expression(),
        )
    )
    db.flush()
    lines = []
    for embedding, content in inputs:
        request_id = uuid4()
        db.add(
            EmbeddingBatchRequest(
                id=request_id,
                batch_id=batch_id,
                embedding_id=embedding.id,
                input_hash=embedding.input_hash,
                active=True,
            )
        )
        lines.append(
            json.dumps(
                {
                    "key": str(request_id),
                    "request": {
                        "content": {"parts": [{"text": content}]},
                        "output_dimensionality": embedding.dimensions,
                    },
                },
                ensure_ascii=False,
            )
        )
    return batch_id, ("\n".join(lines) + "\n").encode("utf-8"), len(inputs), model, job.book_id


def release_failed_embedding_batch(factory: sessionmaker[Session], batch_id: UUID) -> None:
    with factory.begin() as db:
        db.execute(
            update(EmbeddingBatchJob).where(EmbeddingBatchJob.id == batch_id).values(state=EmbeddingBatchState.FAILED)
        )
        release_batch_queue_slot(db, batch_id)
        db.execute(update(EmbeddingBatchRequest).where(EmbeddingBatchRequest.batch_id == batch_id).values(active=False))


def submit_next_embedding_batch(factory: sessionmaker[Session], settings: Settings) -> list[dict[str, object]]:
    with factory() as db:
        if has_active_batch(db):
            return []

        if not db.scalar(
            select(BookBatchJob.book_id)
            .where(BookBatchJob.state == BookBatchJobState.QUEUED, BookBatchJob.kind == "embeddings")
            .limit(1)
        ):
            return []

        if has_outstanding_embedding_batch(db):
            return []

    key = settings.effective_gemini_batch_api_key
    if not key or not key.get_secret_value():
        raise ValueError("Configure VOICE_ARCHIVE_GEMINI_BATCH_API_KEY or VOICE_ARCHIVE_GEMINI_API_KEY")
    tokenizer = LocalTokenizer("gemini-2.5-flash")

    def count_tokens(content: str) -> int:
        count: int | None = tokenizer.count_tokens(content).total_tokens
        if count is None:
            raise ValueError("Local tokenizer returned no token count")
        return count

    with genai.Client(
        api_key=key.get_secret_value(),
        http_options=types.HttpOptions(
            api_version="v1beta",
            timeout=settings.gemini_answer_timeout_seconds * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    ) as client:
        batch_request_input = create_embedding_batch_request_input(factory, settings, count_tokens)
        if batch_request_input is None:
            return []
        batch_id, payload, count, model, book_id = batch_request_input

        try:
            uploaded = client.files.upload(
                file=io.BytesIO(payload),
                config=types.UploadFileConfig(mime_type="application/jsonl", display_name=str(batch_id)),
            )
            if not uploaded.name:
                raise ValueError("Upload returned no file name")
            with factory.begin() as db:
                db.execute(
                    update(EmbeddingBatchJob)
                    .where(EmbeddingBatchJob.id == batch_id)
                    .values(input_file_name=uploaded.name)
                )
        except Exception as exc:
            log_exception(
                logger, f"Embedding input upload failed: batch={batch_id} book={book_id}", exc, settings=settings
            )
            # No batch creation was attempted, so these inputs can safely be retried.
            release_failed_embedding_batch(factory, batch_id)
            raise EmbeddingSubmissionError(
                f"Batch {batch_id}: input upload failed; submission can be retried"
            ) from None

        try:
            created = client.batches.create_embeddings(
                model=model,
                src=types.EmbeddingsBatchJobSource(file_name=uploaded.name),
                config=types.CreateEmbeddingsBatchJobConfig(display_name=str(batch_id)),
            )
            if not created.name:
                raise ValueError("Submission returned no batch name")
            with factory.begin() as db:
                db.execute(
                    update(EmbeddingBatchJob)
                    .where(EmbeddingBatchJob.id == batch_id)
                    .values(provider_name=created.name, state=EmbeddingBatchState.SUBMITTED)
                )
        except errors.ClientError as exc:
            log_exception(
                logger,
                f"Embedding batch rejected: batch={batch_id} book={book_id} model={model}",
                exc,
                settings=settings,
            )
            reason = f"HTTP {exc.code} {exc.status or 'UNKNOWN'}: {exc.message or 'No provider reason supplied'}"
            for configured_key in (settings.gemini_api_key, settings.gemini_batch_api_key):
                if configured_key and (secret := configured_key.get_secret_value()):
                    reason = reason.replace(secret, "[redacted]")
            if exc.code == 408:
                raise EmbeddingSubmissionError(
                    f"Batch {batch_id}: submission timed out ({reason}); claims retained for reconciliation"
                ) from None
            release_failed_embedding_batch(factory, batch_id)
            raise EmbeddingSubmissionError(
                f"Batch {batch_id}: provider rejected submission ({reason}); submission can be retried"
            ) from None
        except Exception as exc:
            log_exception(
                logger,
                f"Embedding submission outcome uncertain: batch={batch_id} book={book_id}",
                exc,
                settings=settings,
            )
            # Includes a lost response or failed local publication after provider acceptance.
            raise EmbeddingSubmissionError(
                f"Batch {batch_id}: submission outcome uncertain; claims retained. "
                "Maintenance will reconcile Google's batch using this display name."
            ) from None
        return [{"batch_id": str(batch_id), "provider_name": created.name, "request_count": count, "book_id": book_id}]
