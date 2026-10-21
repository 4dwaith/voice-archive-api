"""Ownership invariants and timestamp lifecycle independent of service writes."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from voice_archive_api.book_files.cleanup import queue_expired_files
from voice_archive_api.book_files.model import FileCleanupProgress, FileKind, PendingFileDeletion, StoredFile
from voice_archive_api.books import service as book_service
from voice_archive_api.books.model import Book
from voice_archive_api.books.schemas import BookCreate


def add_book(db, title="Owner"):
    now = datetime.now(UTC)
    book = Book(
        title=title, normalized_title=title.lower(), normalized_author="", language="en", created_at=now, updated_at=now
    )
    db.add(book)
    db.flush()
    return book


def add_file(db, *, key="file", kind=FileKind.COVER, book_id=None):
    record = StoredFile(
        kind=kind,
        book_id=book_id,
        storage_key=key,
        byte_size=1,
        file_metadata={},
        created_at=datetime.now(UTC) - timedelta(days=30),
    )
    db.add(record)
    db.flush()
    return record


def test_direct_writes_maintain_unlink_timestamps(test_engine):
    with Session(test_engine) as db:
        book = add_book(db)
        record = add_file(db)
        db.refresh(record)
        assert record.unlinked_at == record.created_at
        db.execute(update(StoredFile).where(StoredFile.id == record.id).values(book_id=book.id))
        db.refresh(record)
        assert record.unlinked_at is None
        before = db.scalar(select(text("statement_timestamp()")))
        db.execute(update(StoredFile).where(StoredFile.id == record.id).values(book_id=None))
        db.refresh(record)
        assert before <= record.unlinked_at <= datetime.now(UTC)
        detached_at = record.unlinked_at
        db.execute(update(StoredFile).where(StoredFile.id == record.id).values(file_metadata={"note": "changed"}))
        db.refresh(record)
        assert record.unlinked_at == detached_at


def test_insert_with_owner_has_no_unlinked_timestamp(test_engine):
    with Session(test_engine) as db:
        record = add_file(db, book_id=add_book(db).id)
        db.refresh(record)
        assert record.unlinked_at is None


def test_database_rejects_duplicate_kind_for_book(test_engine):
    with Session(test_engine) as db:
        book = add_book(db)
        add_file(db, book_id=book.id)
        add_file(db, key="pdf", kind=FileKind.SOURCE_PDF, book_id=book.id)
        with pytest.raises(IntegrityError) as raised:
            add_file(db, key="second-cover", book_id=book.id)
        assert raised.value.orig.diag.constraint_name == "uq_files_book_kind"


def test_multiple_unattached_files_of_same_kind_are_allowed(test_engine):
    with Session(test_engine) as db:
        add_file(db)
        add_file(db, key="second")
        db.commit()


def test_database_rejects_nonexistent_owner(test_engine):
    with Session(test_engine) as db, pytest.raises(IntegrityError) as raised:
        add_file(db, book_id=12345)
    assert raised.value.orig.diag.constraint_name == "fk_files_book_id_books"


def test_database_rejects_unlinked_file_without_timestamp(test_engine):
    with Session(test_engine) as db:
        record = add_file(db)
        with pytest.raises(IntegrityError):
            db.execute(update(StoredFile).where(StoredFile.id == record.id).values(unlinked_at=None))


def test_direct_deletion_succeeds_after_explicit_detachment(test_engine):
    with Session(test_engine) as db:
        record = add_file(db, book_id=add_book(db).id)
        file_id = record.id
        db.commit()
        db.execute(update(StoredFile).where(StoredFile.id == file_id).values(book_id=None))
        db.execute(delete(StoredFile).where(StoredFile.id == file_id))
        db.commit()
        assert db.get(StoredFile, file_id) is None


def test_bulk_delete_rejection_preserves_both_linked_and_unlinked_files(test_engine):
    with Session(test_engine) as db:
        unlinked = add_file(db)
        linked = add_file(db, key="linked", book_id=add_book(db).id)
        file_ids = (unlinked.id, linked.id)
        db.commit()
        with pytest.raises(IntegrityError, match="Cannot delete a linked file"):
            db.execute(delete(StoredFile))
        db.rollback()
        assert set(db.scalars(select(StoredFile.id))) == set(file_ids)


def test_book_deletion_refreshes_cached_file_ownership_before_detaching(test_engine):
    with Session(test_engine, expire_on_commit=False) as db:
        record = add_file(db)
        db.commit()
        book = book_service.create_book(db, BookCreate(title="Owner", language="en", cover_file_id=record.id))
        assert book_service.delete_book(db, book.id)
        assert db.get(StoredFile, record.id) is None
        assert db.get(PendingFileDeletion, record.storage_key) is not None


@pytest.mark.parametrize("action", ["detach", "delete_book"])
def test_old_file_detached_after_scan_gets_new_retention_window(test_engine, action):
    factory = sessionmaker(test_engine)
    with factory.begin() as db:
        book = add_book(db)
        record = add_file(db, book_id=book.id)
        book_id, file_id, key = book.id, record.id, record.storage_key
    now = datetime.now(UTC)
    assert queue_expired_files(factory, now) == 0
    with factory.begin() as db:
        assert db.get(FileCleanupProgress, 1).cleanup_before is not None
        if action == "detach":
            db.execute(update(StoredFile).where(StoredFile.id == file_id).values(book_id=None))
        else:
            db.execute(delete(Book).where(Book.id == book_id))
    with factory() as db:
        record = db.get(StoredFile, file_id)
        assert record is not None
        assert record.book_id is None
        assert record.unlinked_at > now
        detached_at = record.unlinked_at
    assert queue_expired_files(factory, detached_at + timedelta(days=7)) == 0
    assert queue_expired_files(factory, detached_at + timedelta(days=8)) == 1
    with factory() as db:
        assert db.get(StoredFile, file_id) is None
        assert db.get(PendingFileDeletion, key) is not None


def test_ownership_migration_preserves_attached_and_unattached_files(test_engine, migration_config):
    with Session(test_engine) as db:
        book = add_book(db)
        cover = add_file(db, book_id=book.id)
        pdf = add_file(db, key="pdf", kind=FileKind.SOURCE_PDF, book_id=book.id)
        unattached = add_file(db, key="unattached")
        book_id, cover_id, pdf_id, unattached_id = book.id, cover.id, pdf.id, unattached.id
        db.commit()
    command.downgrade(migration_config, "0008_unified_files")
    with test_engine.connect() as connection:
        assert connection.execute(text("SELECT cover_file_id, source_pdf_file_id FROM books")).one() == (
            cover_id,
            pdf_id,
        )
    command.upgrade(migration_config, "head")
    with Session(test_engine) as db:
        for file_id in (cover_id, pdf_id):
            record = db.get(StoredFile, file_id)
            assert record is not None
            assert record.book_id == book_id
            assert record.unlinked_at is None
        record = db.get(StoredFile, unattached_id)
        assert record is not None
        assert record.book_id is None
        assert record.unlinked_at == record.created_at


@pytest.mark.parametrize("field,kind", [("cover_file_id", "source_pdf"), ("source_pdf_file_id", "cover")])
def test_ownership_migration_refuses_wrong_kind_without_losing_legacy_links(test_engine, migration_config, field, kind):
    command.downgrade(migration_config, "0008_unified_files")
    with test_engine.begin() as connection:
        file_id = connection.scalar(
            text("""
            INSERT INTO files (kind, storage_key, byte_size, metadata, created_at)
            VALUES (:kind, 'bad-link', 1, '{}', now()) RETURNING id
        """),
            {"kind": kind},
        )
        connection.execute(
            text(f"""
            INSERT INTO books (title, normalized_title, normalized_author, language, created_at, updated_at, {field})
            VALUES ('Owner', 'owner', '', 'en', now(), now(), :file_id)
        """),
            {"file_id": file_id},
        )
    with pytest.raises(Exception, match="book file kind mismatch"):
        command.upgrade(migration_config, "head")
    with test_engine.connect() as connection:
        assert connection.scalar(text(f"SELECT {field} FROM books")) == file_id
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0008_unified_files"
