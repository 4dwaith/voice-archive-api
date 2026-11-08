"""States for durable book requests and individual embedding batches."""

from enum import StrEnum


class BookBatchJobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EmbeddingBatchState(StrEnum):
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
