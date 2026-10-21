from datetime import UTC, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import psycopg
import pytest
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import CheckConstraint, MetaData, Table, event, func, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from alembic import command
from voice_archive_api.book_files import service
from voice_archive_api.book_files.covers import InvalidImage
from voice_archive_api.book_files.model import CoverFile, PendingFileDeletion, SourcePdfFile
from voice_archive_api.book_files.pdfs import InvalidPdf
from voice_archive_api.books.model import Book
from voice_archive_api.file_handling import LocalStorage


FIRST_BOOK_FILES_REVISION = "0004_book_files"


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "uploads")


def file_content_bytes(kind):
    with BytesIO() as output:
        if kind == "cover":
            with Image.new("RGB", (16, 24), "red") as image:
                image.save(output, "PNG")
        else:
            with PdfWriter() as writer:
                for _ in range(3):
                    writer.add_blank_page(width=100, height=150)
                writer.write(output)
        return output.getvalue()


def create_file(db, storage, kind):
    create = service.create_cover_file if kind == "cover" else service.create_pdf_file
    return create(db, BytesIO(file_content_bytes(kind)), storage, max_bytes=100_000)


def model_for(kind):
    return CoverFile if kind == "cover" else SourcePdfFile


def get_file(db, file_id, kind) -> CoverFile | SourcePdfFile:
    get = service.get_cover_file if kind == "cover" else service.get_pdf_file
    return get(db, file_id)


def remaining_files(storage):
    return [path for path in storage.root.rglob("*") if path.is_file()]


@pytest.mark.parametrize("error", [
    RuntimeError("failure"),
    IntegrityError("", {}, Exception()),
    KeyboardInterrupt(),
], ids=["runtime-error", "integrity-error", "interrupted"])
def test_file_write_cleans_up_and_preserves_errors(error):
    db = Mock(spec=Session)

    with pytest.raises(type(error)) as raised:
        with service.file_write(db):
            raise error

    assert raised.value is error
    db.rollback.assert_called_once_with()


@pytest.mark.parametrize("model", [CoverFile, SourcePdfFile])
def test_file_column_check_names_match_the_migration(test_engine, model):
    # Alembic's generic metadata comparison does not compare check constraints.
    # Verify that inherited columns keep the existing table-specific constraint names.
    declared_names = {
        str(check.name)
        for column in model.__table__.columns
        for check in column.constraints
        if isinstance(check, CheckConstraint)
    }
    migrated_names = {
        check["name"]
        for check in inspect(test_engine).get_check_constraints(model.__tablename__)
    }
    assert declared_names == migrated_names


@pytest.mark.parametrize("kind", ["cover", "source_pdf"])
def test_prepared_files_persist_without_a_book_and_reload(test_engine, storage, kind):
    with Session(test_engine, expire_on_commit=False) as db:
        record = create_file(db, storage, kind)
        file_id = record.id
        assert file_id > 0
        assert db.scalar(select(func.count()).select_from(Book)) == 0

    with Session(test_engine) as db:
        record = get_file(db, file_id, kind)

        # Workaround to avoid warnings from PyCharm
        storage_key = record.storage_key
        assert isinstance(storage_key, str)
        created_at = record.created_at
        assert isinstance(created_at, datetime)

        assert isinstance(record, model_for(kind))
        assert storage_key.startswith("promoted/")
        assert created_at.utcoffset() == timedelta(0)

        with storage.open(storage_key) as stored:
            assert len(stored.read()) == record.byte_size
        if isinstance(record, CoverFile):
            assert (record.width, record.height) == (16, 24)
        else:
            assert record.page_count == 3
        assert remaining_files(storage) == [storage.root / record.storage_key]


@pytest.mark.parametrize("get,error", [
    (service.get_cover_file, service.CoverFileNotFound),
    (service.get_pdf_file, service.SourcePdfFileNotFound),
], ids=["cover", "source-pdf"])
def test_missing_file_record_raises_domain_error(test_engine, get, error):
    with Session(test_engine) as db:
        with pytest.raises(error):
            get(db, 1)


@pytest.mark.parametrize("kind,error", [("cover", InvalidImage), ("source_pdf", InvalidPdf)])
def test_invalid_input_cleanup(test_engine, storage, kind, error):
    create = service.create_cover_file if kind == "cover" else service.create_pdf_file
    with Session(test_engine) as db:
        with pytest.raises(error):
            create(db, BytesIO(b"invalid"), storage, max_bytes=100_000)

        # No record created
        assert db.scalar(select(func.count()).select_from(model_for(kind))) == 0

    # No file created
    assert remaining_files(storage) == []


@pytest.mark.parametrize("kind", ["cover", "source_pdf"])
def test_database_failure_preserves_file(test_engine, storage, kind, monkeypatch):
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
        with pytest.raises(OperationalError, match="connection lost after commit") as raised:
            create_file(db, storage, kind)
        assert raised.value.orig is connection_error
        assert raised.value.connection_invalidated

    # A different connection must see the committed row despite the failed call.
    with Session(test_engine) as db:
        record = db.scalars(select(model_for(kind))).one()
        assert remaining_files(storage) == [storage.root / record.storage_key]
        with storage.open(record.storage_key) as stored:
            assert len(stored.read()) == record.byte_size


def file_record(model, **changes):
    values = dict(
        storage_key="opaque/key", byte_size=100, created_at=datetime.now(UTC),
    )
    if model is CoverFile:
        values.update(width=16, height=24)
    else:
        values.update(page_count=3)
    values.update(changes)
    return model(**values)


@pytest.mark.parametrize("model", [CoverFile, SourcePdfFile])
@pytest.mark.parametrize(
    "source_timezone",
    [UTC, timezone(timedelta(hours=5, minutes=30))],
    ids=["utc", "utc-plus-0530"],
)
def test_file_timestamp_preserves_instant_and_reloads_in_utc(
    test_engine, model, source_timezone,
):
    supplied = datetime(2026, 1, 15, 3, 15, 30, 123456, tzinfo=source_timezone)
    with Session(test_engine) as db:
        record = file_record(model, created_at=supplied)
        db.add(record)
        db.flush()
        file_id = record.id
        db.commit()

    # Reload in a fresh session to check the stored value rather than the assigned one.
    with Session(test_engine) as db:
        record = db.get(model, file_id)
        assert record is not None
        reloaded = record.created_at
        assert isinstance(reloaded, datetime)
        assert reloaded.utcoffset() == timedelta(0)
        assert reloaded == supplied.astimezone(UTC)


@pytest.mark.parametrize(
    "model,changes",
    [
        (CoverFile, {"id": 0}), (SourcePdfFile, {"id": 0}),
        (CoverFile, {"id": -1}), (SourcePdfFile, {"id": -1}),
        (CoverFile, {"storage_key": ""}), (SourcePdfFile, {"storage_key": ""}),
        (CoverFile, {"storage_key": None}), (SourcePdfFile, {"storage_key": None}),
        (CoverFile, {"byte_size": 0}), (SourcePdfFile, {"byte_size": 0}),
        (CoverFile, {"byte_size": None}), (SourcePdfFile, {"byte_size": None}),
        (CoverFile, {"created_at": None}), (SourcePdfFile, {"created_at": None}),
        (CoverFile, {"width": None}), (CoverFile, {"width": 0}),
        (CoverFile, {"height": None}), (CoverFile, {"height": 0}),
        (SourcePdfFile, {"page_count": None}), (SourcePdfFile, {"page_count": 0}),
    ],
    ids=["cover-zero-id", "pdf-zero-id", "cover-negative-id", "pdf-negative-id",
         "cover-empty-key", "pdf-empty-key", "cover-missing-key", "pdf-missing-key",
         "cover-empty-file", "pdf-empty-file",
         "cover-missing-size", "pdf-missing-size",
         "cover-missing-timestamp", "pdf-missing-timestamp",
         "missing-width", "zero-width", "missing-height", "zero-height",
         "missing-page-count", "zero-page-count"],
)
def test_database_rejects_invalid_file_fields(test_engine, model, changes):
    with Session(test_engine) as db:
        db.add(file_record(model, **changes))
        with pytest.raises(IntegrityError):
            db.commit()


@pytest.mark.parametrize("model", [CoverFile, SourcePdfFile])
def test_storage_key_is_unique_within_each_file_table(test_engine, model):
    with Session(test_engine) as db:
        db.add(file_record(model))
        db.commit()
        db.add(file_record(model))
        with pytest.raises(IntegrityError):
            db.commit()


def test_pdf_size_supports_more_than_2gb(test_engine):
    # Test column capacity without allocating or pretending to upload a huge file.
    with Session(test_engine) as db:
        record = file_record(SourcePdfFile, byte_size=5_000_000_000)
        db.add(record)
        db.commit()
        assert db.get(SourcePdfFile, record.id).byte_size == 5_000_000_000


@pytest.mark.parametrize("model", [CoverFile, SourcePdfFile])
def test_files_can_be_added_after_full_downgrade_and_upgrade(
    test_engine, migration_config, model,
):
    command.downgrade(migration_config, "base")
    command.upgrade(migration_config, "head")

    with Session(test_engine) as db:
        record = file_record(model)
        db.add(record)
        db.flush()
        file_id = record.id
        db.commit()

    with Session(test_engine) as db:
        record = db.get(model, file_id)
        assert record is not None
        assert record.id > 0
        assert record.storage_key == "opaque/key"
        assert record.byte_size == 100
        if model is CoverFile:
            assert (record.width, record.height) == (16, 24)
        else:
            assert record.page_count == 3


@pytest.mark.parametrize("model", [CoverFile, SourcePdfFile])
def test_upgrade_from_first_file_revision_preserves_existing_files(
    test_engine, migration_config, model,
):
    command.downgrade(migration_config, FIRST_BOOK_FILES_REVISION)
    historical_files = Table(model.__tablename__, MetaData(), autoload_with=test_engine)
    values = {
        "storage_key": "opaque/historical-key",
        "byte_size": 100,
        "created_at": datetime(2026, 1, 15, 3, 15, 30, 123456, tzinfo=UTC),
    }
    if model is CoverFile:
        values.update(width=16, height=24)
    else:
        values.update(page_count=3)

    # Insert using the historical schema, independently of future ORM changes.
    with test_engine.begin() as connection:
        file_id = connection.execute(
            historical_files.insert().values(**values).returning(historical_files.c.id)
        ).scalar_one()

    command.upgrade(migration_config, "head")
    with Session(test_engine) as db:
        record = db.get(model, file_id)
        assert record is not None
        for field, expected in values.items():
            assert getattr(record, field) == expected, field


@pytest.mark.parametrize("kind", ["cover", "source_pdf"])
def test_failed_explicit_deletion_commit_preserves_file_record_and_bytes(test_engine, storage, kind):
    with Session(test_engine) as db:
        record = create_file(db, storage, kind)
        file_id, storage_key = record.id, record.storage_key

        def interrupt_commit(session):
            raise RuntimeError("deletion commit interrupted")

        event.listen(db, "before_commit", interrupt_commit)
        delete_file = service.delete_cover_file if kind == "cover" else service.delete_pdf_file
        with pytest.raises(RuntimeError, match="deletion commit interrupted"):
            delete_file(db, file_id)
    with Session(test_engine) as db:
        assert db.get(model_for(kind), file_id) is not None
        assert db.get(PendingFileDeletion, storage_key) is None
    assert storage.exists(storage_key)


