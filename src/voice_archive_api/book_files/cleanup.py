"""Expire abandoned prepared files and retry pending deletions."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, select, tuple_
from sqlalchemy.orm import Session, sessionmaker

from voice_archive_api.book_files.model import CoverFile, FileCleanupProgress, PendingFileDeletion, SourcePdfFile
from voice_archive_api.book_files.service import queue_file_deletion
from voice_archive_api.books.model import Book
from voice_archive_api.file_handling import Storage

logger = logging.getLogger(__name__)
UNLINKED_RETENTION = timedelta(days=7)
SCAN_OVERLAP = timedelta(days=1)
BATCH_SIZE = 100


@dataclass
class CleanupResult:
    prepared_files_expired: int = 0
    files_deleted: int = 0
    failures: int = 0


def queue_expired_files(factory: sessionmaker[Session], now: datetime) -> int:
    """Scan indexed age windows, retaining one day of overlap for late commits."""
    total = 0
    cutoff = now - UNLINKED_RETENTION
    for model, link, boundary_field in (
        (CoverFile, Book.cover_file_id, "cover_cleanup_before"),
        (SourcePdfFile, Book.source_pdf_file_id, "source_pdf_cleanup_before"),
    ):
        linked_to_book = exists(select(Book.id).where(link == model.id))
        conditions = [model.created_at < cutoff, ~linked_to_book]  # Older than the cut-off, and not linked to any books
        with factory() as db:
            progress = db.get(FileCleanupProgress, 1)
            boundary: datetime = getattr(progress, boundary_field)
        if boundary is not None:
            # Search for files a little bit before the boundary to allow for time drift.
            conditions.append(model.created_at >= boundary - SCAN_OVERLAP)  # Newer than the cleanup boundary

        # Stores the (created_at, id) of the last record processed
        cursor = None

        # Paginate with cursor. It breaks out of the loop when there are no more records to process.
        while True:
            with factory.begin() as db:
                # Lock the progress record to prevent concurrent cleanup runs from interfering with
                # and overwriting each other's progress.
                progress = db.get(FileCleanupProgress, 1, with_for_update=True)
                query = select(model).where(*conditions)

                if cursor is not None:
                    query = query.where(tuple_(model.created_at, model.id) > cursor)

                # Select one batch of records - limited by batch size, offset by the cursor,
                # skipping anything that's currently being modified by the application
                records = list(db.scalars(
                    query.order_by(model.created_at, model.id).limit(BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                ))

                if not records:
                    # All records in the database which were eligible for delete have been deleted
                    #
                    # A skipped row must not disappear behind the saved boundary.
                    # Only update the progress record's boundary if no unlinked candidate remains.
                    if not db.scalar(select(exists().where(*conditions))):
                        previous = getattr(progress, boundary_field)

                        if previous is None or cutoff > previous:
                            # The cutoff is updated as the latest boundary. This gets committed
                            # to the DB after we exit the DB's with block
                            setattr(progress, boundary_field, cutoff)
                    break

                # There are records to process. Update the last record's values as the cursor
                cursor = (records[-1].created_at, records[-1].id)

                # Recheck the whole locked batch in one fresh READ COMMITTED query.
                # The row is locked with FOR UPDATE which prevents any foreign key references,
                # but it's possible that the row was linked to after the condition was met, but before we acquired the lock.
                linked_ids = set(db.scalars(
                    select(link).where(link.in_([record.id for record in records]))
                ))

                for record in records:
                    if record.id in linked_ids:
                        continue

                    # Won't cause an N + 1 query, since we're not reading from the database.
                    # These writes will be accumulated in memory, and sent forward in one flush
                    queue_file_deletion(db, record, now=now)
                    total += 1
            # The database transaction is committed here
    return total


def delete_pending_files(factory: sessionmaker[Session], storage: Storage) -> tuple[int, int]:
    """Retry each pending deletion once per run, retaining failures for later runs."""
    deleted = failures = 0

    # Ordering isn't important, neither is a guarantee that all records will be processed. Prioritize throughput,
    # since this is a recurring job that can retry any skipped records in the next run
    cursor = ""
    while True:
        with factory.begin() as db:
            pending = list(db.scalars(
                select(PendingFileDeletion).where(PendingFileDeletion.storage_key > cursor)
                .order_by(PendingFileDeletion.storage_key).limit(BATCH_SIZE)
                .with_for_update(skip_locked=True)
            ))
            if not pending:
                break
            for record in pending:
                cursor = record.storage_key
                try:
                    storage.delete(record.storage_key)
                except Exception:
                    failures += 1
                    logger.exception("File deletion failed; retaining pending deletion for retry: %s", record.storage_key)
                else:
                    db.delete(record)
                    deleted += 1

            # If this commit fails after unlinking, database rows remain. Storage.delete
            # is idempotent, so a later run can safely acknowledge them again.
    return deleted, failures


def run_promoted_cleanup(
    factory: sessionmaker[Session], storage: Storage, *, now: datetime | None = None,
) -> CleanupResult:
    result = CleanupResult()
    result.prepared_files_expired = queue_expired_files(factory, now or datetime.now(UTC))
    result.files_deleted, failures = delete_pending_files(factory, storage)
    result.failures += failures
    logger.info("Promoted-file cleanup completed: %s", result)
    return result
