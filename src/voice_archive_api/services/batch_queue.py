"""Shared durable book work and one global asynchronous batch submission slot."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from voice_archive_api.db.models.batch_queue import BookBatchJob
from voice_archive_api.db.operations import acquire_named_transaction_lock, create_wall_clock_expression
from voice_archive_api.logic.batches import BookBatchJobState


def lock_batch_queue(db: Session) -> None:
    acquire_named_transaction_lock(db, "book-batch-queue")


def enqueue_book_batch_job(db: Session, book_id: int, kind: str) -> None:
    """Caller locks the queue, then domain rows, and owns the transaction."""
    job = db.get(BookBatchJob, (book_id, kind), with_for_update=True)
    if job is None:
        db.add(BookBatchJob(book_id=book_id, kind=kind, state=BookBatchJobState.QUEUED))
    elif job.state in (BookBatchJobState.COMPLETED, BookBatchJobState.FAILED):
        job.state = BookBatchJobState.QUEUED
        job.requested_at = create_wall_clock_expression()


def has_active_batch(db: Session) -> bool:
    return db.scalar(select(BookBatchJob.book_id).where(BookBatchJob.active_batch_id.is_not(None)).limit(1)) is not None


def release_batch_queue_slot(db: Session, batch_id: UUID) -> None:
    db.execute(
        update(BookBatchJob)
        .where(BookBatchJob.active_batch_id == batch_id)
        .values(state=BookBatchJobState.QUEUED, active_batch_id=None)
    )
