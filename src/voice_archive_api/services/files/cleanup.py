"""Expire abandoned file records, delete orphan bytes, and retry pending deletions."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import islice

from sqlalchemy import exists, select, tuple_
from sqlalchemy.orm import Session, sessionmaker

from voice_archive_api.core.logging import log_exception
from voice_archive_api.db.models.file import FileCleanupProgress, PendingFileDeletion, StoredFile
from voice_archive_api.file_handling import Storage
from voice_archive_api.services.files.service import queue_file_deletion

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
    conditions = [StoredFile.unlinked_at < cutoff, StoredFile.book_id.is_(None), StoredFile.section_id.is_(None)]
    with factory() as db:
        progress = db.get(FileCleanupProgress, 1)
        assert progress is not None
        boundary = progress.cleanup_before
    if boundary is not None:
        conditions.append(StoredFile.unlinked_at >= boundary - SCAN_OVERLAP)

    # Stores the (unlinked_at, id) of the last record processed
    cursor = None

    # Paginate with cursor. It breaks out of the loop when there are no more records to process.
    while True:
        with factory.begin() as db:
            # Lock the progress record to prevent concurrent cleanup runs from interfering with
            # and overwriting each other's progress.
            progress = db.get(FileCleanupProgress, 1, with_for_update=True)
            assert progress is not None
            query = select(StoredFile).where(*conditions)

            if cursor is not None:
                query = query.where(tuple_(StoredFile.unlinked_at, StoredFile.id) > cursor)

            # Select one batch of records - limited by batch size, offset by the cursor,
            # skipping anything that's currently being modified by the application
            records = list(
                db.scalars(
                    query.order_by(StoredFile.unlinked_at, StoredFile.id)
                    .limit(BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                )
            )

            if not records:
                # All records in the database which were eligible for delete have been deleted
                #
                # A skipped row must not disappear behind the saved boundary.
                # Only update the progress record's boundary if no unlinked candidate remains.
                if not db.scalar(select(exists().where(*conditions))):
                    previous = progress.cleanup_before

                    if previous is None or cutoff > previous:
                        # The cutoff is updated as the latest boundary. This gets committed
                        # to the DB after we exit the DB's with block
                        progress.cleanup_before = cutoff
                break

            # There are records to process. Update the last record's values as the cursor
            cursor = (records[-1].unlinked_at, records[-1].id)

            for record in records:
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
            pending = list(
                db.scalars(
                    select(PendingFileDeletion)
                    .where(PendingFileDeletion.storage_key > cursor)
                    .order_by(PendingFileDeletion.storage_key)
                    .limit(BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                )
            )
            if not pending:
                break
            for record in pending:
                cursor = record.storage_key
                try:
                    storage.delete(record.storage_key)
                except Exception as exc:
                    failures += 1
                    log_exception(
                        logger, f"File deletion failed; retaining pending deletion for retry: {record.storage_key}", exc
                    )
                else:
                    db.delete(record)
                    deleted += 1

            # If this commit fails after unlinking, database rows remain. Storage.delete
            # is idempotent, so a later run can safely acknowledge them again.
    return deleted, failures


def run_promoted_cleanup(
    factory: sessionmaker[Session],
    storage: Storage,
    *,
    now: datetime | None = None,
) -> CleanupResult:
    result = CleanupResult()
    result.prepared_files_expired = queue_expired_files(factory, now or datetime.now(UTC))
    result.files_deleted, failures = delete_pending_files(factory, storage)
    result.failures += failures
    logger.info("Promoted-file cleanup completed: %s", result)
    return result


def delete_orphan_files(factory: sessionmaker[Session], storage: Storage, now: datetime) -> tuple[int, int]:
    """Delete aged promoted bytes absent from the file table, in bounded batches.

    Promoted keys are immutable and never reused. The seven-day grace period
    protects the short promotion-to-database-commit window of an active upload.
    Database lookup failures abort deletion of that batch. Failed deletes are
    rediscovered by the next full scan.
    """
    deleted = failures = 0
    candidates = storage.iter_promoted(before=now - UNLINKED_RETENTION)
    try:
        while True:
            batch = list(islice(candidates, BATCH_SIZE))
            if not batch:
                break
            # Check the file table immediately before deleting this batch.
            # No full-table key set or per-file database query is needed.
            with factory() as db:
                referenced = set(db.scalars(select(StoredFile.storage_key).where(StoredFile.storage_key.in_(batch))))
            for key in batch:
                if key in referenced:
                    continue
                try:
                    storage.delete(key)
                except Exception as exc:
                    failures += 1
                    log_exception(logger, f"Orphan file deletion failed; will retry next scan: {key}", exc)
                else:
                    deleted += 1
    finally:
        # Local enumeration holds a directory handle; close it on database failure too.
        candidates.close()
    return deleted, failures


def run_orphan_cleanup(
    factory: sessionmaker[Session],
    storage: Storage,
    *,
    now: datetime | None = None,
) -> CleanupResult:
    result = CleanupResult()
    result.files_deleted, result.failures = delete_orphan_files(factory, storage, now or datetime.now(UTC))
    logger.info("Orphan-file cleanup completed: %s", result)
    return result
