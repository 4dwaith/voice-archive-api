"""Orphan-file cleanup uses isolated PostgreSQL and real local files."""

import os
from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import Mock

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from voice_archive_api.db.models.file import StoredFile
from voice_archive_api.file_handling import LocalStorage
from voice_archive_api.logic.files import FileKind
from voice_archive_api.services.files import cleanup

NOW = datetime(2026, 10, 21, tzinfo=UTC)


@pytest.fixture
def factory(test_engine):
    return sessionmaker(test_engine, expire_on_commit=False)


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "uploads")


def save_new_file(storage, days=8):
    key = storage.promote(storage.put_temporary(BytesIO(b"stored bytes"), max_bytes=100))
    timestamp = (NOW - timedelta(days=days)).timestamp()
    os.utime(storage.local_path(key), (timestamp, timestamp))
    return key


def add_reference(factory, key, model):
    values = {"file_metadata": {"width": 16, "height": 24} if model == FileKind.COVER else {"page_count": 3}}
    with factory.begin() as db:
        db.add(StoredFile(kind=model, storage_key=key, byte_size=12, created_at=NOW, **values))


def test_scan_batches_file_lookups(factory, storage, monkeypatch, test_engine):
    # Set up the orphaned and non-orphaned files for testing
    monkeypatch.setattr(cleanup, "BATCH_SIZE", 2)
    orphan = save_new_file(storage)
    boundary = save_new_file(storage, 7)
    recent = save_new_file(storage, 6)
    cover, pdf = save_new_file(storage, 30), save_new_file(storage, 30)
    add_reference(factory, cover, FileKind.COVER)
    add_reference(factory, pdf, FileKind.SOURCE_PDF)
    temporary = storage.put_temporary(BytesIO(b"temporary"), max_bytes=100)
    queries = []

    # Run the orphan cleanup while counting the database queries
    def count_queries(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT"):
            queries.append(statement)

    event.listen(test_engine, "before_cursor_execute", count_queries)
    try:
        result = cleanup.run_orphan_cleanup(factory, storage, now=NOW)
    finally:
        event.remove(test_engine, "before_cursor_execute", count_queries)

    assert result.files_deleted == 1
    assert result.failures == 0
    assert not storage.exists(orphan)
    assert all(storage.exists(key) for key in (boundary, recent, cover, pdf, temporary))
    # Three eligible files in two batches: two queries, not one per file.
    assert len(queries) == 2
    assert cleanup.run_orphan_cleanup(factory, storage, now=NOW).files_deleted == 0


def test_failed_deletion_does_not_block_other_orphans_and_retries_next_run(factory, storage):
    blocked, other = save_new_file(storage), save_new_file(storage)
    failing = Mock(wraps=storage)

    def delete(key):
        if key == blocked:
            raise OSError("file busy")
        storage.delete(key)

    failing.delete.side_effect = delete
    result = cleanup.run_orphan_cleanup(factory, failing, now=NOW)
    assert (result.files_deleted, result.failures) == (1, 1)
    assert storage.exists(blocked) and not storage.exists(other)
    assert cleanup.run_orphan_cleanup(factory, storage, now=NOW).files_deleted == 1


def test_database_failure_stops_deletion_and_closes_enumeration(storage):
    orphan = save_new_file(storage)
    closed = []
    source = storage.iter_promoted(before=NOW)

    def candidates(**kwargs):
        try:
            yield from source
        finally:
            closed.append(True)

    wrapped_storage = Mock(wraps=storage)
    wrapped_storage.iter_promoted.side_effect = candidates
    factory = Mock(side_effect=RuntimeError("database unavailable"))
    with pytest.raises(RuntimeError, match="database unavailable"):
        cleanup.run_orphan_cleanup(factory, wrapped_storage, now=NOW)
    wrapped_storage.delete.assert_not_called()
    assert storage.exists(orphan)
    assert closed == [True]


def test_reference_created_after_enumeration_is_seen_before_deletion(factory, storage):
    key = save_new_file(storage)
    wrapped_storage = Mock(wraps=storage)

    def candidates(**kwargs):
        yield key
        add_reference(factory, key, FileKind.SOURCE_PDF)

    wrapped_storage.iter_promoted.side_effect = candidates
    assert cleanup.run_orphan_cleanup(factory, wrapped_storage, now=NOW).files_deleted == 0
    wrapped_storage.delete.assert_not_called()
    assert storage.exists(key)
