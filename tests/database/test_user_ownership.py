import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from tests.database.content_helpers import add_audio, add_section, add_segment
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.file import StoredFile
from voice_archive_api.db.models.series import Series
from voice_archive_api.db.models.user import User
from voice_archive_api.services.books.schemas import BookCreate, BookPatch
from voice_archive_api.services.books.service import BookNotFound, book_write, create_book, patch_book
from voice_archive_api.services.series.schemas import SeriesCreate
from voice_archive_api.services.series.service import SeriesNotFound, create_series, replace_series_books


@pytest.mark.parametrize("changes", [{"is_enabled": True}, {"password_hash": "not-a-real-hash"}])
def test_placeholder_has_no_email_and_cannot_have_login_credentials(test_engine, changes):
    with Session(test_engine) as db:
        user = db.scalars(select(User)).one()

        assert (user.display_name, user.email, user.password_hash, user.is_enabled) == ("Unassigned", None, None, False)

        for field, value in changes.items():
            setattr(user, field, value)

        with pytest.raises(IntegrityError):
            db.commit()


def test_email_is_case_insensitively_unique(test_engine):
    with Session(test_engine) as db:
        db.add(User(email="reader@example.com", is_enabled=False))
        db.commit()

        db.add(User(email="READER@EXAMPLE.COM", is_enabled=False))

        with pytest.raises(IntegrityError):
            db.commit()


def test_migration_assigns_unassigned_owner_and_preserves_content_and_files(test_engine, migration_config):
    with Session(test_engine) as db:
        series = create_series(db, SeriesCreate(title="Existing series"))
        book = create_book(db, BookCreate(title="Existing book", language="en", series_id=series.id))
        section = add_section(db, book.id)
        add_segment(db, book.id, section_id=section.id)
        audio = add_audio(db, section_id=section.id)
        unlinked = add_audio(db)
        db.commit()
        book_id, series_id, section_id, audio_id, unlinked_id = (
            book.id,
            series.id,
            section.id,
            audio.id,
            unlinked.id,
        )
    with test_engine.connect() as db:
        original = {
            table: db.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()
            for table in ("sections", "segments", "files")
        }
    command.downgrade(migration_config, "0027_window_lexical")

    command.upgrade(migration_config, "head")

    with Session(test_engine) as db:
        user = db.scalars(select(User)).one()
        assert (user.display_name, user.email, user.password_hash, user.is_enabled) == ("Unassigned", None, None, False)
        assert db.get(Book, book_id).owner_user_id == db.get(Series, series_id).owner_user_id == user.id
        assert db.get(StoredFile, audio_id).section_id == section_id

        unlinked_file = db.get(StoredFile, unlinked_id)
        assert unlinked_file is not None
        assert unlinked_file.section_id is None

    with test_engine.connect() as db:
        for table, rows in original.items():
            assert db.execute(text(f"SELECT * FROM {table} ORDER BY id")).all() == rows
    assert "owner_user_id" not in {column["name"] for column in inspect(test_engine).get_columns("files")}


@pytest.mark.parametrize("duplicate_is_public", [False, True])
def test_identity_is_unique_per_owner_including_public_collection(test_engine, duplicate_is_public):
    with Session(test_engine) as db:
        book = create_book(db, BookCreate(title="Shared title", language="en"))
        user = User(email="owner@example.com", is_enabled=False)
        db.add(user)
        db.flush()
        book.owner_user_id = user.id
        db.commit()

        public = create_book(db, BookCreate(title="Shared title", language="en"))

        assert public.owner_user_id is None
        with pytest.raises(IntegrityError), db.begin_nested():
            db.execute(
                text(
                    "INSERT INTO books (owner_user_id, title, normalized_title, normalized_author, language, created_at, updated_at) "
                    "SELECT owner_user_id, title, normalized_title, normalized_author, language, created_at, updated_at "
                    "FROM books WHERE id=:id"
                ),
                {"id": public.id if duplicate_is_public else book.id},
            )


@pytest.mark.parametrize("owner_is_public", [False, True])
def test_series_rejects_different_owners_in_service_and_database(test_engine, owner_is_public):
    with Session(test_engine) as db:
        user = User(email="owner@example.com", is_enabled=False)
        db.add(user)
        db.flush()
        book = create_book(db, BookCreate(title="Private", language="en"))
        series = create_series(db, SeriesCreate(title="Series"))
        if owner_is_public:
            series.owner_user_id = user.id
        else:
            book.owner_user_id = user.id
        db.commit()
        book_id, series_id = book.id, series.id

        with pytest.raises(BookNotFound):
            replace_series_books(db, series_id, [book_id])

        with pytest.raises(SeriesNotFound):
            patch_book(db, book_id, BookPatch(series_id=series_id))

        with pytest.raises(SeriesNotFound) as failure, book_write(db):
            db.execute(
                text("UPDATE books SET series_id=:series, series_index=1 WHERE id=:book"),
                {"series": series_id, "book": book_id},
            )
            db.commit()

        assert isinstance(failure.value.__cause__, IntegrityError)
        assert db.get(Book, book_id).series_id is None


def test_downgrade_does_not_discard_accounts_when_global_identity_conflicts(test_engine, migration_config):
    with Session(test_engine) as db:
        book = create_book(db, BookCreate(title="Same", language="en"))
        user = User(email="owner@example.com", is_enabled=False)
        db.add(user)
        db.flush()
        book.owner_user_id = user.id
        db.commit()
        create_book(db, BookCreate(title="Same", language="en"))

    with pytest.raises(IntegrityError):
        command.downgrade(migration_config, "0027_window_lexical")

    assert "users" in inspect(test_engine).get_table_names()
    with test_engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0028_users_and_ownership"
