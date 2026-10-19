from datetime import UTC, datetime, timedelta, timezone
from random import Random
from unittest.mock import Mock

import pytest
from sqlalchemy import MetaData, Table, select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from voice_archive_api.books import service
from voice_archive_api.books.model import Book
from voice_archive_api.books.schemas import BookCreate


FIRST_BOOK_REVISION = "0002_book_metadata"

def payload(title="Book", **kwargs):
    return BookCreate(title=title, language="en", **kwargs)


@pytest.mark.parametrize("error", [RuntimeError("failure"), IntegrityError("", {}, Exception())])
def test_book_write_preserves_unrelated_errors(error):
    # Testing the context manager.
    # Book write should only intercept duplicate as a custom exception, not touching other errors

    db = Mock(spec=Session)
    with pytest.raises(type(error)) as raised:
        with service.book_write(db):
            raise error
    assert raised.value is error


def test_maximum_unicode_identity_fits_postgres_index(test_engine):
    # A defensive test to ensure that a hard, incompressible value for both title and author doesn't overflow
    # the joint index. This is currently ensured by using a hash of the title and author when indexing.

    random = Random(42)
    title = "".join(chr(random.randrange(0x4E00, 0x9FFF)) for _ in range(750))
    author = "".join(chr(random.randrange(0x4E00, 0x9FFF)) for _ in range(450))
    with Session(test_engine) as db:
        book = service.create_book(db, payload(title, author=author))
        assert book.title == title
        assert book.author == author
        with pytest.raises(service.DuplicateBook):
            service.create_book(db, payload(title, author=author))


@pytest.mark.parametrize(
    "source_timezone",
    [UTC, timezone(timedelta(hours=5, minutes=30))],
    ids=["utc", "utc-plus-0530"],
)
def test_book_timestamp_columns_preserve_instants_and_reload_in_utc(
    test_engine, source_timezone
):
    created_at = datetime(2026, 1, 15, 3, 15, 30, 123456, tzinfo=source_timezone)
    updated_at = created_at + timedelta(hours=2)
    # Bypass service-generated UTC timestamps to exercise the column mappings
    # with both UTC and non-UTC inputs. The engine configures DB sessions in UTC;
    # the instant must survive, while the original offset need not be retained.
    with Session(test_engine) as db:
        book = Book(
            title="Timezone example",
            normalized_title="timezone example",
            normalized_author="",
            language="en",
            created_at=created_at,
            updated_at=updated_at,
        )
        db.add(book)
        db.flush()
        book_id = book.id
        db.commit()

    # A fresh session forces a database read rather than inspecting the object
    # that was originally assigned the aware Python datetime values.
    with Session(test_engine) as db:
        book = db.get(Book, book_id)
        assert book is not None
        for field, supplied in (("created_at", created_at), ("updated_at", updated_at)):
            reloaded = getattr(book, field)
            assert reloaded.utcoffset() == timedelta(0), field
            assert reloaded == supplied.astimezone(UTC), field


def test_service_delete_is_repeatable_and_deleted_ids_are_not_reused(test_engine):
    with Session(test_engine) as db:
        book_id = service.create_book(db, payload()).id
        assert service.delete_book(db, book_id) is True
        assert service.delete_book(db, book_id) is False
        replacement = service.create_book(db, payload())
        assert (
            replacement.id > book_id
        )  # PostgreSQL identity sequences do not recycle IDs.


def test_unique_constraint_handles_a_writer_winning_after_precheck(
    test_engine, monkeypatch
):
    original_check = service.check_duplicate
    intervened = False

    def competing_write(db, title, author, exclude_id=None):
        nonlocal intervened
        original_check(db, title, author, exclude_id)
        if not intervened:
            intervened = True
            with Session(test_engine) as other:
                service.create_book(other, payload("Same"))

    monkeypatch.setattr(service, "check_duplicate", competing_write)
    with Session(test_engine) as db:
        with pytest.raises(service.DuplicateBook):
            service.create_book(db, payload(" SAME "))
        assert len(list(db.scalars(select(Book)))) == 1
        assert service.create_book(db, payload("Different")).id > 0


def test_database_duplicate_is_translated_to_http_conflict(client, monkeypatch):
    assert (
        client.post(
            "/api/v1/books", json={"title": "Book", "language": "en"}
        ).status_code
        == 201
    )
    # Force the write to reach the constraint, as when a precheck saw stale data.
    monkeypatch.setattr(service, "check_duplicate", lambda *args, **kwargs: None)
    response = client.post("/api/v1/books", json={"title": " BOOK ", "language": "en"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_book"


def test_book_write_rolls_back_failed_commit_and_recovers_session(test_engine):
    now = datetime.now(UTC)
    with Session(test_engine) as db:
        book = Book(
            title="Original", normalized_title="original", normalized_author="",
            language="en", created_at=now, updated_at=now,
        )
        db.add(book)
        db.commit()
        book_id = book.id

        with pytest.raises(IntegrityError):
            with service.book_write(db):
                book.description = "Discard me"
                db.flush()  # Make a real change before the later failure.
                book.title = ""  # Violate the database's nonempty-title constraint.
                db.commit()

        # Check before closing the session: closing it could mask a missing
        # rollback in book_write. A failed transaction must be usable again.
        db.refresh(book)
        assert book.title == "Original"
        assert book.description is None
        book.author = "Keep me"
        db.commit()

    with Session(test_engine) as db:
        book = db.get(Book, book_id)
        assert book is not None
        assert book.author == "Keep me"
        assert book.description is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "x" * 751),
        ("title", ""),
        ("title", None),
        ("title", "a\x00" + "b" * 800),
        ("author", "x" * 451),
        ("description", "x" * 15_001),
        ("language", None),
        ("language", ""),
        ("normalized_title", ""),
        ("normalized_author", None),
        ("id", 0),
        ("id", -1),
    ],
)
def test_database_enforces_constraints(test_engine, field, value):
    now = datetime.now(UTC)
    values = {"title": "Valid", "normalized_title": "valid", "author": None, "normalized_author": "",
              "description": None, "language": "en", "created_at": now, "updated_at": now, field: value}
    with Session(test_engine) as db:
        db.add(Book(**values))
        with pytest.raises((IntegrityError, DataError)):
            db.commit()
        db.rollback()
        assert db.scalar(select(Book.id)) is None


def test_books_can_be_added_after_full_downgrade_and_upgrade(test_engine, migration_config):
    command.downgrade(migration_config, "base")
    command.upgrade(migration_config, "head")
    now = datetime.now(UTC)
    with Session(test_engine) as db:
        book = Book(
            title="Rebuilt library", normalized_title="rebuilt library",
            normalized_author="", language="en", created_at=now, updated_at=now,
        )
        db.add(book)
        db.flush()
        book_id = book.id
        db.commit()
    with Session(test_engine) as db:
        book = db.get(Book, book_id)
        assert book is not None
        assert book.id > 0
        assert book.title == "Rebuilt library"

def test_upgrade_from_first_book_revision_preserves_existing_books(test_engine, migration_config):
    # Checks that ever since the books table was introduced, all migrations preserve existing books.
    command.downgrade(migration_config, FIRST_BOOK_REVISION)

    # Get the table definition as it was during the first revision.
    # This is to decouple the test from the current schema.
    historical_books = Table("books", MetaData(), autoload_with=test_engine)
    now = datetime.now(UTC)
    values = {
        "title": "Book", "normalized_title": "book", "author": "Author",
        "normalized_author": "author", "description": "x" * 15000,
        "language": "kn", "created_at": now, "updated_at": now,
    }

    # Raw SQL insert to preserve the historical data.
    with test_engine.begin() as connection:
        book_id = connection.execute(
            historical_books.insert().values(**values).returning(historical_books.c.id)
        ).scalar_one()

    command.upgrade(migration_config, "head")

    with Session(test_engine) as db:
        book = db.get(Book, book_id)
        assert book is not None
        for field, expected in values.items():
            assert getattr(book, field) == expected, field
