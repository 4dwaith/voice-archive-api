from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import Mock

import pytest
from sqlalchemy import event, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from alembic import command
from tests.database.content_helpers import add_audio, add_section
from tests.database.files.test_file_ownership import add_book
from tests.database.session_helpers import create_trusted_session
from tests.file_processing.file_samples import cover_bytes, pdf_bytes
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.file import FileCleanupProgress, PendingFileDeletion, StoredFile
from voice_archive_api.file_handling import LocalStorage
from voice_archive_api.logic.files import FileKind
from voice_archive_api.services.access import AccessContext
from voice_archive_api.services.books import service as book_service
from voice_archive_api.services.books.schemas import BookCreate, BookPatch
from voice_archive_api.services.files import cleanup, service

# Freeze the time to this point - all files and rows prepared will be relative to this point
NOW = datetime(2026, 10, 21, tzinfo=UTC)
FIRST_PENDING_DELETES_REVISION = "0006_pending_file_deletions"


@pytest.fixture
def factory(test_engine):
    return sessionmaker(
        test_engine, expire_on_commit=False, info={"access": AccessContext(user_id=None, is_trusted=True)}
    )


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "uploads")


def prepared_file(factory, storage, kind, age=timedelta(days=8)):
    with factory() as db:
        payload = {"cover": cover_bytes, "source_pdf": pdf_bytes}[kind]()
        record = service.create_file(db, FileKind(kind), BytesIO(payload), storage, max_bytes=100_000)
        record.created_at = NOW - age
        record.unlinked_at = NOW - age
        db.commit()
        return record


@pytest.mark.parametrize("kind", ["cover", "source_pdf"])
def test_exact_retention_boundary_is_preserved(factory, storage, kind):
    record = prepared_file(factory, storage, kind, timedelta(days=7))
    assert cleanup.queue_expired_files(factory, NOW) == 0
    with factory() as db:
        assert db.get(type(record), record.id) is not None
        assert db.get(PendingFileDeletion, record.storage_key) is None


@pytest.mark.parametrize("kind", ["cover", "source_pdf"])
def test_failed_expiration_commit_preserves_file_record_and_bytes(factory, storage, kind):
    record = prepared_file(factory, storage, kind)

    @event.listens_for(factory, "before_commit")
    def interrupt_commit(db):
        if any(isinstance(record, PendingFileDeletion) for record in db.new):
            raise RuntimeError("commit interrupted")

    with pytest.raises(RuntimeError, match="commit interrupted"):
        cleanup.run_promoted_cleanup(factory, storage, now=NOW)
    with factory() as db:
        assert db.get(type(record), record.id) is not None
        assert db.get(PendingFileDeletion, record.storage_key) is None
    assert storage.exists(record.storage_key)


@pytest.mark.parametrize("kind", ["cover", "source_pdf"])
def test_failed_file_delete_retains_pending_deletion_and_succeeds_on_next_run(factory, storage, kind):
    record = prepared_file(factory, storage, kind)
    failing_storage = Mock(wraps=storage)
    failing_storage.delete.side_effect = OSError("file busy")

    result = cleanup.run_promoted_cleanup(factory, failing_storage, now=NOW)

    assert result.prepared_files_expired == result.failures == 1
    assert result.files_deleted == 0
    with factory() as db:
        assert db.get(type(record), record.id) is None
        assert db.get(PendingFileDeletion, record.storage_key) is not None
    assert storage.exists(record.storage_key)
    # The same pending key is attempted once per run, rather than spinning.
    failing_storage.delete.assert_called_once_with(record.storage_key)

    result = cleanup.run_promoted_cleanup(factory, storage, now=NOW)
    assert result.prepared_files_expired == result.failures == 0
    assert result.files_deleted == 1
    with factory() as db:
        assert db.get(PendingFileDeletion, record.storage_key) is None
    assert not storage.exists(record.storage_key)


@pytest.mark.parametrize("kind", ["cover", "source_pdf"])
def test_failed_acknowledgment_after_unlink_can_retry_an_absent_file(factory, storage, kind):
    record = prepared_file(factory, storage, kind)
    cleanup.queue_expired_files(factory, NOW)

    @event.listens_for(factory, "before_commit", once=True)
    def interrupt_acknowledgment(*_):
        raise RuntimeError("acknowledgment interrupted")

    with pytest.raises(RuntimeError, match="acknowledgment interrupted"):
        cleanup.delete_pending_files(factory, storage)
    # Bytes are gone, but the uncommitted acknowledgment leaves a durable retry.
    assert not storage.exists(record.storage_key)
    with factory() as db:
        assert db.get(PendingFileDeletion, record.storage_key) is not None
    assert cleanup.delete_pending_files(factory, storage) == (1, 0)
    with factory() as db:
        assert db.get(PendingFileDeletion, record.storage_key) is None


@pytest.mark.parametrize("kind", ["cover", "source_pdf"])
def test_concurrent_book_save_prevents_expiration(factory, storage, kind):
    record = prepared_file(factory, storage, kind)
    with factory.begin() as saving:
        # An uncommitted ownership update locks the file itself.
        book = Book(
            title="Being saved",
            normalized_title="being saved",
            normalized_author="",
            language="en",
            created_at=NOW,
            updated_at=NOW,
        )
        saving.add(book)
        saving.flush()
        saving.execute(update(StoredFile).where(StoredFile.id == record.id).values(book_id=book.id))
        result = cleanup.run_promoted_cleanup(factory, storage, now=NOW)
        assert result.prepared_files_expired == result.files_deleted == 0
    with factory() as db:
        assert db.get(type(record), record.id) is not None
    assert storage.exists(record.storage_key)


def test_cleanup_processes_multiple_batches(factory, storage, monkeypatch):
    monkeypatch.setattr(cleanup, "BATCH_SIZE", 1)
    records = [prepared_file(factory, storage, kind) for kind in ("cover", "cover", "source_pdf")]
    result = cleanup.run_promoted_cleanup(factory, storage, now=NOW)
    assert result.prepared_files_expired == result.files_deleted == 3
    assert all(not storage.exists(record.storage_key) for record in records)


@pytest.mark.parametrize("kind", ["cover", "source_pdf"])
def test_cleanup_never_queries_book_references(factory, storage, test_engine, kind):
    records = [prepared_file(factory, storage, kind) for _ in range(5)]
    reference_queries = []

    def capture_reference_query(connection, cursor, statement, parameters, context, executemany):
        if "FROM books" in statement:
            reference_queries.append(statement)

    event.listen(test_engine, "before_cursor_execute", capture_reference_query)
    try:
        assert cleanup.queue_expired_files(factory, NOW) == len(records)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture_reference_query)
    assert reference_queries == []
    with factory() as db:
        for record in records:
            assert db.get(type(record), record.id) is None
            assert db.get(PendingFileDeletion, record.storage_key) is not None


def test_pending_deletion_key_cannot_be_empty(factory):
    with factory() as db:
        db.add(PendingFileDeletion(storage_key="", queued_at=NOW))
        with pytest.raises(IntegrityError):
            db.commit()


def test_pending_deletions_survive_upgrade_from_their_first_revision(factory, migration_config):
    command.downgrade(migration_config, FIRST_PENDING_DELETES_REVISION)
    with factory.begin() as db:
        db.add(PendingFileDeletion(storage_key="promoted/pending", queued_at=NOW))
    command.upgrade(migration_config, "head")
    with factory() as db:
        record = db.get(PendingFileDeletion, "promoted/pending")
        assert record is not None
        assert record.queued_at == NOW


@pytest.mark.parametrize("kind", ["cover", "source_pdf"])
def test_scan_revisits_one_day_of_history_and_leaves_recent_files_for_later(
    factory,
    storage,
    kind,
):
    boundary = prepared_file(factory, storage, kind, timedelta(days=7))
    cleanup.queue_expired_files(factory, NOW)
    with factory() as db:
        assert db.get(FileCleanupProgress, 1).cleanup_before == NOW - timedelta(days=7)
        assert db.get(type(boundary), boundary.id) is not None

    # Simulate uploads becoming visible after the completed scan. Include the
    # exact lower boundary of the overlap; older late commits are outside policy.
    overlap = prepared_file(factory, storage, kind, timedelta(days=8))
    outside_overlap = prepared_file(factory, storage, kind, timedelta(days=9))
    assert cleanup.queue_expired_files(factory, NOW + timedelta(days=1)) == 2
    with factory() as db:
        assert db.get(type(boundary), boundary.id) is None
        assert db.get(type(overlap), overlap.id) is None
        assert db.get(type(outside_overlap), outside_overlap.id) is not None
        assert db.get(FileCleanupProgress, 1).cleanup_before == NOW - timedelta(days=6)


@pytest.mark.parametrize("kind", ["cover", "source_pdf"])
def test_locked_candidate_does_not_advance_boundary(factory, storage, kind):
    record = prepared_file(factory, storage, kind)
    with factory.begin() as other:
        other.get(type(record), record.id, with_for_update=True)
        assert cleanup.queue_expired_files(factory, NOW) == 0
        with factory() as db:
            assert db.get(FileCleanupProgress, 1).cleanup_before is None
    assert cleanup.queue_expired_files(factory, NOW) == 1


def test_interrupted_scan_retries_committed_batches_before_advancing(factory, storage, monkeypatch):
    monkeypatch.setattr(cleanup, "BATCH_SIZE", 1)
    first = prepared_file(factory, storage, "cover")
    second = prepared_file(factory, storage, "cover")
    commits = 0

    def interrupt_second_batch(db):
        nonlocal commits
        commits += 1
        if commits == 2:
            raise RuntimeError("second batch interrupted")

    event.listen(factory, "before_commit", interrupt_second_batch)
    with pytest.raises(RuntimeError, match="second batch interrupted"):
        cleanup.queue_expired_files(factory, NOW)
    event.remove(factory, "before_commit", interrupt_second_batch)
    with factory() as db:
        assert db.get(StoredFile, first.id) is None
        assert db.get(PendingFileDeletion, first.storage_key) is not None
        assert db.get(StoredFile, second.id) is not None
        cleanup_progress = db.get(FileCleanupProgress, 1)
        assert cleanup_progress is not None
        # The boundary was not updated because the deletion was interrupted
        assert cleanup_progress.cleanup_before is None

    # A second attempt at cleanup was able to queue the second file
    assert cleanup.queue_expired_files(factory, NOW) == 1


def test_locked_file_blocks_the_shared_scan_boundary(factory, storage):
    record = prepared_file(factory, storage, "source_pdf")
    with factory.begin() as other:
        other.get(StoredFile, record.id, with_for_update=True)
        cleanup.queue_expired_files(factory, NOW)
        with factory() as db:
            progress = db.get(FileCleanupProgress, 1)
            assert progress.cleanup_before is None


@pytest.mark.parametrize("action", ["replace", "clear", "delete"])
def test_failed_book_commit_preserves_links_file_records_and_bytes(factory, storage, action):
    old = prepared_file(factory, storage, "cover")
    replacement = prepared_file(factory, storage, "cover")
    with factory() as db:
        book_id = book_service.create_book(
            db,
            BookCreate(
                title="Owner",
                language="en",
                cover_file_id=old.id,
            ),
        ).id

    @event.listens_for(factory, "before_commit")
    def interrupt_commit(db):
        assert db.get(PendingFileDeletion, old.storage_key) is not None
        raise RuntimeError("book commit interrupted")

    with factory() as db, pytest.raises(RuntimeError, match="book commit interrupted"):
        if action == "delete":
            book_service.delete_book(db, book_id)
        else:
            book_service.patch_book(
                db,
                book_id,
                BookPatch(
                    cover_file_id=replacement.id if action == "replace" else None,
                ),
            )
    with factory() as db:
        assert db.get(Book, book_id).cover_file_id == old.id
        assert db.get(StoredFile, old.id) is not None
        assert db.get(StoredFile, replacement.id) is not None
        assert db.get(PendingFileDeletion, old.storage_key) is None
    assert storage.exists(old.storage_key)


def test_progress_migration_starts_with_null_boundaries_and_preserves_files(
    factory,
    storage,
    migration_config,
):
    record = prepared_file(factory, storage, "cover")
    command.downgrade(migration_config, FIRST_PENDING_DELETES_REVISION)
    command.upgrade(migration_config, "head")
    with factory() as db:
        progress = db.get(FileCleanupProgress, 1)
        assert progress is not None
        assert progress.cleanup_before is None
        assert db.get(StoredFile, record.id).storage_key == record.storage_key
    assert storage.exists(record.storage_key)


def test_expiration_ignores_section_owned_audio_and_queues_unlinked(test_engine):
    with create_trusted_session(test_engine) as db:
        root = add_section(db, add_book(db).id)
        linked = add_audio(db, section_id=root.id)
        unlinked = add_audio(db)
        linked_id, unlinked_id = linked.id, unlinked.id
        unlinked_key = unlinked.storage_key
        db.commit()
    assert (
        cleanup.queue_expired_files(
            sessionmaker(test_engine, info={"access": AccessContext(user_id=None, is_trusted=True)}), datetime.now(UTC)
        )
        == 1
    )
    with create_trusted_session(test_engine) as db:
        assert db.get(StoredFile, linked_id) is not None
        assert db.get(StoredFile, unlinked_id) is None
        assert db.get(PendingFileDeletion, unlinked_key) is not None
