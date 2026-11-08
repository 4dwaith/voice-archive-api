"""Coordinate CLI submission and periodic embedding maintenance."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from voice_archive_api.core.config import Settings
from voice_archive_api.core.logging import log_exception
from voice_archive_api.db.models.batch_queue import BookBatchJob
from voice_archive_api.services.embeddings.collect_results import collect_embedding_batches
from voice_archive_api.services.embeddings.submit_batches import (
    EmbeddingSubmissionError,
    enqueue_book_embeddings,
    submit_next_embedding_batch,
)

logger = logging.getLogger(__name__)


def submit_book_embedding_batches(
    factory: sessionmaker[Session], settings: Settings, book_id: int
) -> dict[str, object]:
    enqueue_book_embeddings(factory, book_id)
    batches = submit_next_embedding_batch(factory, settings)
    with factory() as db:
        state = db.scalar(
            select(BookBatchJob.state).where(BookBatchJob.book_id == book_id, BookBatchJob.kind == "embeddings")
        )
    return {"book_id": book_id, "state": state, "batches": batches}


def run_embedding_maintenance(factory: sessionmaker[Session], settings: Settings) -> int:
    failures = collect_embedding_batches(factory, settings)
    try:
        submit_next_embedding_batch(factory, settings)
    except EmbeddingSubmissionError:
        failures += 1
    except ValueError as exc:
        log_exception(logger, "Embedding queue advancement failed", exc, settings=settings)
        failures += 1
    return failures
