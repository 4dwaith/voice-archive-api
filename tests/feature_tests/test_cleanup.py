"""Exercise file cleanup through the maintenance CLI in a separate Python process."""

import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from pytest_bdd import given, parsers, scenarios, then, when
from sqlalchemy.orm import Session

from tests.file_processing.file_samples import cover_bytes, pdf_bytes
from voice_archive_api.book_files import service as file_service
from voice_archive_api.book_files.model import FileCleanupProgress, FileKind, PendingFileDeletion, StoredFile
from voice_archive_api.books import service as book_service
from voice_archive_api.books.schemas import BookCreate, BookPatch
from voice_archive_api.file_handling import LocalStorage

scenarios("../features/maintenance")


@dataclass
class CleanupScenario:
    file: StoredFile | None = None
    book_id: int | None = None
    temporary_key: str | None = None
    orphan_key: str | None = None
    result: subprocess.CompletedProcess | None = None


@pytest.fixture
def state():
    return CleanupScenario()


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "uploads")


@pytest.fixture
def cli_environment(storage):
    environment = os.environ.copy()
    # Temporary-only scenarios must not connect to any database, including the
    # shared test database. Promoted setup replaces these with its isolated URL.
    environment.update(
        {
            "VOICE_ARCHIVE_ENVIRONMENT": "test",
            "VOICE_ARCHIVE_UPLOAD_ROOT": str(storage.root),
            "VOICE_ARCHIVE_POSTGRES_USER": "unused",
            "VOICE_ARCHIVE_POSTGRES_PASSWORD": "unused",
            "VOICE_ARCHIVE_POSTGRES_HOST": "127.0.0.1",
            "VOICE_ARCHIVE_POSTGRES_PORT": "1",
            "VOICE_ARCHIVE_POSTGRES_DB": "unused",
        }
    )
    return environment


@given("the promoted file database is ready")
def configure_database(test_engine, cli_environment):
    url = test_engine.url
    cli_environment.update(
        {
            "VOICE_ARCHIVE_POSTGRES_USER": url.username,
            "VOICE_ARCHIVE_POSTGRES_PASSWORD": url.password,
            "VOICE_ARCHIVE_POSTGRES_HOST": url.host,
            "VOICE_ARCHIVE_POSTGRES_PORT": str(url.port),
            "VOICE_ARCHIVE_POSTGRES_DB": url.database,
        }
    )


@given(parsers.parse("an unlinked {kind} uploaded {days:d} days ago"))
def uploaded_file(test_engine, storage, state, kind, days):
    with Session(test_engine, expire_on_commit=False) as db:
        file_kind = {"cover": FileKind.COVER, "source PDF": FileKind.SOURCE_PDF}[kind]
        payload = {FileKind.COVER: cover_bytes, FileKind.SOURCE_PDF: pdf_bytes}[file_kind]()
        record = file_service.create_file(db, file_kind, BytesIO(payload), storage, max_bytes=100_000)
        record.created_at = datetime.now(UTC) - timedelta(days=days)
        db.commit()
        state.file = record


def file_link(state):
    return file_service.BOOK_FILE_FIELDS[state.file.kind]


@given("the uploaded file is linked to a book")
def linked_file(test_engine, state):
    with Session(test_engine) as db:
        state.book_id = book_service.create_book(
            db,
            BookCreate(
                title="File owner",
                language="en",
                **{file_link(state): state.file.id},
            ),
        ).id


@given("maintenance has already scanned that upload date")
def past_scan_boundary(test_engine):
    with Session(test_engine) as db:
        progress = db.get(FileCleanupProgress, 1)
        progress.cleanup_before = datetime.now(UTC) - timedelta(days=7)
        db.commit()


@given(parsers.parse("the book's file is {action}"))
def remove_book_file(test_engine, storage, state, action):
    with Session(test_engine) as db:
        if action == "removed by deleting the book":
            book_service.delete_book(db, state.book_id)
        elif action == "cleared":
            book_service.patch_book(db, state.book_id, BookPatch(**{file_link(state): None}))
        else:
            assert action == "replaced"
            payload = {FileKind.COVER: cover_bytes, FileKind.SOURCE_PDF: pdf_bytes}[state.file.kind]()
            replacement = file_service.create_file(db, state.file.kind, BytesIO(payload), storage, max_bytes=100_000)
            book_service.patch_book(db, state.book_id, BookPatch(**{file_link(state): replacement.id}))


@given("deletion of the uploaded file is pending from an earlier run")
def pending_deletion(test_engine, state):
    with Session(test_engine) as db:
        file_service.queue_file_deletion(db, db.get(type(state.file), state.file.id), now=datetime.now(UTC))
        db.commit()


@given(parsers.parse("a temporary upload inactive for {hours:d} hours"))
@given(parsers.parse("a temporary upload inactive for {hours:d} hour"))
def temporary_upload(storage, state, hours):
    state.temporary_key = storage.put_temporary(BytesIO(b"temporary upload"), max_bytes=100)
    timestamp = (datetime.now(UTC) - timedelta(hours=hours)).timestamp()
    os.utime(storage.local_path(state.temporary_key), (timestamp, timestamp))


@when(parsers.parse("I run {job} file maintenance once"))
def run_maintenance(cli_environment, state, job):
    command = [sys.executable, "-m", "voice_archive_api.maintenance", "--once", "--job", job]
    state.result = subprocess.run(command, env=cli_environment, capture_output=True, text=True, timeout=30)


@given(parsers.parse("a file without a database record inactive for {days:d} days"))
def orphaned_file(storage, state, days):
    state.orphan_key = storage.promote(storage.put_temporary(BytesIO(b"orphaned file"), max_bytes=100))
    timestamp = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    os.utime(storage.local_path(state.orphan_key), (timestamp, timestamp))


@given(parsers.parse("the uploaded file is inactive for {days:d} days"))
def age_uploaded_file(storage, state, days):
    timestamp = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    os.utime(storage.local_path(state.file.storage_key), (timestamp, timestamp))


@then("the orphaned file is removed")
def orphan_removed(storage, state):
    assert not storage.exists(state.orphan_key)


@then("the orphaned file remains available")
def orphan_preserved(storage, state):
    assert storage.exists(state.orphan_key)


@then("maintenance succeeds")
def maintenance_succeeded(state):
    assert state.result.returncode == 0, state.result.stderr


@then("the uploaded file is removed")
def uploaded_file_removed(test_engine, storage, state):
    assert not storage.exists(state.file.storage_key)
    with Session(test_engine) as db:
        assert db.get(type(state.file), state.file.id) is None
        assert db.get(PendingFileDeletion, state.file.storage_key) is None


@then("the uploaded file remains available")
def uploaded_file_preserved(test_engine, storage, state):
    assert storage.exists(state.file.storage_key)
    with Session(test_engine) as db:
        assert db.get(type(state.file), state.file.id) is not None


@then("the temporary upload is removed")
def temporary_upload_removed(storage, state):
    assert not storage.exists(state.temporary_key)


@then("the temporary upload remains available")
def temporary_upload_preserved(storage, state):
    assert storage.exists(state.temporary_key)
