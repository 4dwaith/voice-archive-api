"""Database guarantees for linking independently prepared files to books."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from voice_archive_api.book_files import service as file_service
from voice_archive_api.book_files.model import CoverFile, SourcePdfFile
from voice_archive_api.books import service
from voice_archive_api.books.model import Book
from voice_archive_api.books.schemas import BookCreate, BookPatch


@pytest.fixture
def cover_file_id(test_engine) -> int:
    with Session(test_engine, expire_on_commit=False) as db:
        cover = CoverFile(
            storage_key="promoted/prepared-cover", byte_size=100,
            created_at=datetime.now(UTC), width=16, height=24,
        )
        db.add(cover)
        db.commit()
        file_id = cover.id
        assert isinstance(file_id, int)
        return file_id


@pytest.fixture
def source_pdf_file_id(test_engine) -> int:
    with Session(test_engine, expire_on_commit=False) as db:
        pdf = SourcePdfFile(
            storage_key="promoted/prepared-pdf", byte_size=100,
            created_at=datetime.now(UTC), page_count=3,
        )
        db.add(pdf)
        db.commit()
        file_id = pdf.id
        assert isinstance(file_id, int)
        return file_id


def assert_competing_claim_is_rejected(
    test_engine, monkeypatch, *, file_id, file_field_key, get_file, expected_error,
):
    original_get_file_record = getattr(file_service, get_file)

    def competing_claim(db, requested_id):
        # This is executed before the book has been updated in the database

        monkeypatch.setattr(file_service, get_file, original_get_file_record) # Restore lookup for the competing service call to avoid recursion.

        # It has fetched the file record from the database ... (1/3)
        record = original_get_file_record(db, requested_id)

        with Session(test_engine) as other:
            # ... and before the book updates, a new book with the same file has been created in the database ... (2/3)
            service.create_book(other, BookCreate(
                title="Winning book", language="en", **{file_field_key: file_id},
            ))
        return record

    with Session(test_engine) as db:
        book = service.create_book(db, BookCreate(title="Interrupted book", language="en"))
        book_id = book.id
        original_updated_at = book.updated_at

        monkeypatch.setattr(file_service, get_file, competing_claim)

        with pytest.raises(expected_error):
            # ... so it fails when updating a book with the same file, since the file is unique. (3/3)
            service.patch_book(db, book_id, BookPatch(
                description="Discard this", **{file_field_key: file_id},
            ))

    with Session(test_engine) as db:
        # After the error has interrupted our book update, it should still have the same values as when it was created.
        book = db.get(Book, book_id)
        assert isinstance(book, Book)
        assert {
            "description": book.description,
            "updated_at": book.updated_at,
            file_field_key: getattr(book, file_field_key),
        } == {
            "description": None,
            "updated_at": original_updated_at,
            file_field_key: None,
        }
        owner = db.scalar(select(Book).where(getattr(Book, file_field_key) == file_id))
        assert owner.title == "Winning book"


def assert_removal_after_lookup_rejects_save(
    test_engine, monkeypatch, *, model, file_id, file_field_key, get_file, file_not_found_error,
):
    original_get_file_record = getattr(file_service, get_file)

    def removed_after_lookup(db, requested_id):
        # This is executed before the book has been saved in the database

        # It has fetched the file record from the database ... (1/3)
        record = original_get_file_record(db, requested_id)
        with Session(test_engine) as other:
            # ... and then the file got deleted from the database ... (2/3)
            other.execute(delete(model).where(model.id == requested_id))
            other.commit()
        return record

    monkeypatch.setattr(file_service, get_file, removed_after_lookup)
    with Session(test_engine) as db:
        # ... so it fails when creating a book with the deleted file (3/3)
        with pytest.raises(file_not_found_error):
            service.create_book(db, BookCreate(
                title="Rejected", language="en", **{file_field_key: file_id},
            ))
    with Session(test_engine) as db:
        assert db.scalar(select(func.count()).select_from(Book)) == 0


def assert_linked_file_cannot_be_deleted(test_engine, *, model, file_id, file_field_key):
    with Session(test_engine) as db:
        book_id = service.create_book(db, BookCreate(
            title="Owner", language="en", **{file_field_key: file_id},
        )).id
    with Session(test_engine) as db:
        with pytest.raises(IntegrityError):
            db.execute(delete(model).where(model.id == file_id))
            db.commit()
    with Session(test_engine) as db:
        assert db.get(model, file_id) is not None
        assert getattr(db.get(Book, book_id), file_field_key) == file_id


def test_cover_claim_by_another_writer_is_rejected(test_engine, monkeypatch, cover_file_id):
    assert_competing_claim_is_rejected(
        test_engine, monkeypatch, file_id=cover_file_id,
        file_field_key="cover_file_id", get_file="get_cover_file",
        expected_error=service.CoverFileAlreadyLinked,
    )


def test_source_pdf_claim_by_another_writer_is_rejected(test_engine, monkeypatch, source_pdf_file_id):
    assert_competing_claim_is_rejected(
        test_engine, monkeypatch, file_id=source_pdf_file_id,
        file_field_key="source_pdf_file_id", get_file="get_pdf_file",
        expected_error=service.SourcePdfFileAlreadyLinked,
    )


def test_cover_removed_after_lookup_rejects_book_save(test_engine, monkeypatch, cover_file_id):
    assert_removal_after_lookup_rejects_save(
        test_engine, monkeypatch, model=CoverFile, file_id=cover_file_id,
        file_field_key="cover_file_id", get_file="get_cover_file",
        file_not_found_error=file_service.CoverFileNotFound,
    )


def test_source_pdf_removed_after_lookup_rejects_book_save(test_engine, monkeypatch, source_pdf_file_id):
    assert_removal_after_lookup_rejects_save(
        test_engine, monkeypatch, model=SourcePdfFile, file_id=source_pdf_file_id,
        file_field_key="source_pdf_file_id", get_file="get_pdf_file",
        file_not_found_error=file_service.SourcePdfFileNotFound,
    )


def test_linked_cover_cannot_be_deleted_directly(test_engine, cover_file_id):
    assert_linked_file_cannot_be_deleted(
        test_engine, model=CoverFile, file_id=cover_file_id, file_field_key="cover_file_id",
    )


def test_linked_source_pdf_cannot_be_deleted_directly(test_engine, source_pdf_file_id):
    assert_linked_file_cannot_be_deleted(
        test_engine, model=SourcePdfFile, file_id=source_pdf_file_id,
        file_field_key="source_pdf_file_id",
    )
