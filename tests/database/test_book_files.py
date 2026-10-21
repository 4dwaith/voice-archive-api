from datetime import UTC, datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import Mock

import psycopg
import pytest
from sqlalchemy import CheckConstraint, MetaData, Table, event, func, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from alembic import command
from tests.file_processing.file_samples import cover_bytes, pdf_bytes
from voice_archive_api.book_files import service
from voice_archive_api.book_files.covers import InvalidImage
from voice_archive_api.book_files.model import FileKind, PendingFileDeletion, StoredFile
from voice_archive_api.book_files.pdfs import InvalidPdf
from voice_archive_api.books.model import Book
from voice_archive_api.file_handling import LocalStorage

FIRST_BOOK_FILES_REVISION = "0004_book_files"


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "uploads")


def create_file(db, storage, kind):
    payload = {FileKind.COVER: cover_bytes, FileKind.SOURCE_PDF: pdf_bytes}[kind]()
    return service.create_file(db, kind, BytesIO(payload), storage, max_bytes=100_000)


def file_record(kind=FileKind.COVER, **changes):
    values = dict(kind=kind, storage_key="opaque/key", byte_size=100, created_at=datetime.now(UTC), file_metadata={})
    values.update(changes)
    return StoredFile(**values)


def remaining_files(storage):
    return [path for path in storage.root.rglob("*") if path.is_file()]


@pytest.mark.parametrize("error", [RuntimeError("failure"), IntegrityError("", {}, Exception()), KeyboardInterrupt()])
def test_file_write_rolls_back_and_preserves_errors(error):
    db = Mock(spec=Session)
    with pytest.raises(type(error)) as raised, service.file_write(db):
        raise error
    assert raised.value is error
    db.rollback.assert_called_once_with()


def test_file_column_check_names_match_the_migration(test_engine):
    declared_constraints = {
        str(check.name)
        for column in StoredFile.__table__.columns
        for check in column.constraints
        if isinstance(check, CheckConstraint)
    }
    database_constraints = {check["name"] for check in inspect(test_engine).get_check_constraints("files")}
    assert declared_constraints <= database_constraints
    assert "ck_files_file_kind" in database_constraints  # Derived from naming convention


@pytest.mark.parametrize("kind", list(FileKind))
def test_prepared_files_persist_without_a_book_and_reload(test_engine, storage, kind):
    with Session(test_engine) as db:
        record = create_file(db, storage, kind)
        file_id = record.id
        assert db.scalar(select(func.count()).select_from(Book)) == 0
    with Session(test_engine) as db:
        record = service.get_file(db, file_id, expected_kind=kind)
        assert record.kind == kind
        assert record.created_at.utcoffset() == timedelta(0)
        assert (
            record.file_metadata
            == {
                FileKind.COVER: {"width": 16, "height": 24},
                FileKind.SOURCE_PDF: {"page_count": 3},
            }[kind]
        )
        with storage.open(record.storage_key) as stored:
            assert len(stored.read()) == record.byte_size
        assert remaining_files(storage) == [storage.root / record.storage_key]


def test_missing_file_record_raises_domain_error(test_engine):
    with Session(test_engine) as db, pytest.raises(service.FileNotFound):
        service.get_file(db, 1)


@pytest.mark.parametrize("kind,error", [(FileKind.COVER, InvalidImage), (FileKind.SOURCE_PDF, InvalidPdf)])
def test_invalid_input_cleanup(test_engine, storage, kind, error):
    with Session(test_engine) as db:
        with pytest.raises(error):
            service.create_file(db, kind, BytesIO(b"invalid"), storage, max_bytes=100_000)
        assert db.scalar(select(func.count()).select_from(StoredFile)) == 0
    assert not remaining_files(storage)


@pytest.mark.parametrize("kind", list(FileKind))
def test_database_failure_after_commit_preserves_file(test_engine, storage, kind, monkeypatch):
    commit = test_engine.dialect.do_commit
    connection_error = psycopg.OperationalError("connection lost after commit")

    def commit_then_disconnect(connection):
        # Persist the transaction, but report a lost connection to SQLAlchemy
        # before it can regard the commit as successful.
        commit(connection)
        connection.driver_connection.close()
        raise connection_error

    with monkeypatch.context() as patch, Session(test_engine) as db:
        patch.setattr(test_engine.dialect, "do_commit", commit_then_disconnect)
        with pytest.raises(OperationalError) as raised:
            create_file(db, storage, kind)
        assert raised.value.orig is connection_error
        assert raised.value.connection_invalidated

    # A different connection must see the committed row despite the failed call.
    with Session(test_engine) as db:
        record = db.scalars(select(StoredFile)).one()
        assert remaining_files(storage) == [storage.root / record.storage_key]
        with storage.open(record.storage_key) as stored:
            assert len(stored.read()) == record.byte_size


@pytest.mark.parametrize("source_timezone", [UTC, timezone(timedelta(hours=5, minutes=30))])
def test_file_timestamp_preserves_instant(test_engine, source_timezone):
    supplied = datetime(2026, 1, 15, 3, 15, tzinfo=source_timezone)
    with Session(test_engine) as db:
        record = file_record(created_at=supplied)
        db.add(record)
        db.commit()
        file_id = record.id
    with Session(test_engine) as db:
        assert db.get(StoredFile, file_id).created_at == supplied.astimezone(UTC)


@pytest.mark.parametrize(
    "changes",
    [
        {"id": 0},
        {"id": -1},
        {"storage_key": ""},
        {"storage_key": None},
        {"byte_size": 0},
        {"byte_size": None},
        {"created_at": None},
        {"kind": None},
    ],
)
def test_database_rejects_invalid_common_file_fields(test_engine, changes):
    with Session(test_engine) as db:
        db.add(file_record(**changes))
        with pytest.raises(IntegrityError):
            db.commit()


def test_database_rejects_unknown_kind_even_without_python_validation(test_engine):
    from sqlalchemy import text

    with test_engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text("""INSERT INTO files (kind, storage_key, byte_size, metadata, created_at)
            VALUES ('unknown', 'key', 1, '{}', now())""")
        )


def test_storage_key_is_unique_across_kinds(test_engine):
    with Session(test_engine) as db:
        db.add(file_record())
        db.commit()
        db.add(file_record(FileKind.SOURCE_PDF))
        with pytest.raises(IntegrityError):
            db.commit()


def test_metadata_does_not_require_kind_specific_database_fields(test_engine):
    with Session(test_engine) as db:
        db.add(file_record(file_metadata={"custom": "value"}))
        db.commit()
        assert db.scalars(select(StoredFile)).one().file_metadata == {"custom": "value"}


def test_file_size_supports_more_than_2gb(test_engine):
    with Session(test_engine) as db:
        record = file_record(FileKind.SOURCE_PDF, byte_size=5_000_000_000)
        db.add(record)
        db.commit()
        assert db.get(StoredFile, record.id).byte_size == 5_000_000_000


@pytest.mark.parametrize("kind", list(FileKind))
def test_files_can_be_added_after_full_downgrade_and_upgrade(test_engine, migration_config, kind):
    command.downgrade(migration_config, "base")
    command.upgrade(migration_config, "head")
    with Session(test_engine) as db:
        record = file_record(kind)
        db.add(record)
        db.commit()
        assert record.id > 0


@pytest.mark.parametrize(
    "kind,historical_table_name,metadata",
    [
        (FileKind.COVER, "cover_files", {"width": 16, "height": 24}),
        (FileKind.SOURCE_PDF, "source_pdf_files", {"page_count": 3}),
    ],
    ids=["cover", "source-pdf"],
)
def test_upgrade_from_first_file_revision_preserves_existing_files(
    test_engine,
    migration_config,
    kind,
    historical_table_name,
    metadata,
):
    command.downgrade(migration_config, FIRST_BOOK_FILES_REVISION)
    historical_files = Table(historical_table_name, MetaData(), autoload_with=test_engine)
    values = {
        "storage_key": "opaque/historical-key",
        "byte_size": 100,
        "created_at": datetime(2026, 1, 15, 3, 15, 30, 123456, tzinfo=UTC),
    }

    # Insert using the historical schema, independently of the current ORM model.
    with test_engine.begin() as connection:
        connection.execute(historical_files.insert().values(**values, **metadata))

    command.upgrade(migration_config, "head")
    with Session(test_engine) as db:
        # IDs can be remapped when the old tables merge; the storage key identifies
        # the same stored file across revisions.
        record = db.scalars(
            select(StoredFile).where(
                StoredFile.storage_key == values["storage_key"],
            )
        ).one()
        assert record.kind == kind
        for field, expected in values.items():
            assert getattr(record, field) == expected, field
        assert record.file_metadata == metadata


@pytest.mark.parametrize("kind", list(FileKind))
def test_failed_explicit_deletion_commit_preserves_file_record_and_bytes(test_engine, storage, kind):
    with Session(test_engine) as db:
        record = create_file(db, storage, kind)
        file_id, key = record.id, record.storage_key

        def interrupt_commit(session):
            raise RuntimeError("deletion commit interrupted")

        event.listen(db, "before_commit", interrupt_commit)
        with pytest.raises(RuntimeError, match="deletion commit interrupted"):
            service.delete_file(db, file_id)
    with Session(test_engine) as db:
        assert db.get(StoredFile, file_id) is not None
        assert db.get(PendingFileDeletion, key) is None
    assert storage.exists(key)
