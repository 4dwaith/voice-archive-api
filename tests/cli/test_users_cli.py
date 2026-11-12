from unittest.mock import Mock

import pytest
from psycopg import OperationalError as DriverOperationalError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tests.database.content_helpers import add_audio, add_section
from voice_archive_api.cli import users
from voice_archive_api.core.config import Settings
from voice_archive_api.core.passwords import password_hasher
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.file import StoredFile
from voice_archive_api.db.models.series import Series
from voice_archive_api.db.models.user import User
from voice_archive_api.services.books.schemas import BookCreate
from voice_archive_api.services.books.service import create_book
from voice_archive_api.services.series.schemas import SeriesCreate
from voice_archive_api.services.series.service import create_series


@pytest.fixture
def account_cli(application, monkeypatch):
    monkeypatch.setattr(users, "Settings", lambda: application.state.settings)
    monkeypatch.setattr(users, "getpass", lambda _: "test-password")
    return users.run


def test_create_duplicate_and_password_reset(account_cli, test_engine, monkeypatch, capsys):
    assert account_cli(["create", " Reader@Example.COM ", "--display-name", "आद्वैत 📚"]) == 0

    with Session(test_engine) as db:
        user = db.scalar(select(User).where(User.email == "reader@example.com"))
        assert user.is_enabled
        assert user.display_name == "आद्वैत 📚"
        assert user.password_hash.startswith("$argon2id$")
        assert password_hasher.verify("test-password", user.password_hash)

    assert account_cli(["create", "READER@EXAMPLE.COM"]) == 1

    assert "already exists" in capsys.readouterr().err
    monkeypatch.setattr(users, "getpass", lambda _: "replacement-password")

    assert account_cli(["reset-password", "READER@EXAMPLE.COM"]) == 0

    with Session(test_engine) as db:
        user = db.scalar(select(User).where(User.email == "reader@example.com"))
        assert password_hasher.verify("replacement-password", user.password_hash)
        assert not password_hasher.verify("test-password", user.password_hash)
    output = capsys.readouterr()
    assert "replacement-password" not in output.out + output.err


@pytest.mark.parametrize("passwords", [("", ""), ("secret", "different")])
def test_invalid_password_does_not_open_database(monkeypatch, passwords):
    engine = Mock()
    responses = iter(passwords)
    monkeypatch.setattr(users, "getpass", lambda _: next(responses))
    monkeypatch.setattr(users, "create_database_engine", engine)

    assert users.run(["create", "reader@example.com"]) == 1

    engine.assert_not_called()


def test_reset_cannot_enable_placeholder(account_cli, test_engine):
    assert account_cli(["reset-password", "Unassigned"]) == 1

    with Session(test_engine) as db:
        user = db.scalar(select(User).where(User.email.is_(None)))
        assert not user.is_enabled
        assert user.password_hash is None


def test_invalid_email_is_rejected_before_password_prompt_or_database_access(monkeypatch):
    prompt = Mock()
    engine = Mock()
    monkeypatch.setattr(users, "getpass", prompt)
    monkeypatch.setattr(users, "create_database_engine", engine)

    assert users.run(["create", "Unassigned"]) == 1

    prompt.assert_not_called()
    engine.assert_not_called()


def test_display_name_is_optional_and_does_not_identify_accounts(account_cli, test_engine):
    assert account_cli(["create", "reader@example.com"]) == 0
    assert account_cli(["create", "another@example.com", "--display-name", "Unassigned"]) == 0

    with Session(test_engine) as db:
        assert db.scalar(select(User).where(User.email == "reader@example.com")).display_name is None
        assert db.scalar(select(User).where(User.email == "another@example.com")).display_name == "Unassigned"


@pytest.mark.parametrize("has_public_duplicate,expected_result,is_public", [(False, 0, True), (True, 1, False)])
def test_publish_standalone_book_is_atomic(account_cli, test_engine, has_public_duplicate, expected_result, is_public):
    with Session(test_engine) as db:
        book = create_book(db, BookCreate(title="Demo", language="en"))
        book.owner_user_id = db.scalar(select(User.id).where(User.email.is_(None)))
        db.commit()
        book_id = book.id
        if has_public_duplicate:
            create_book(db, BookCreate(title="Demo", language="en"))

    assert account_cli(["publish-demo", "--book-id", str(book_id)]) == expected_result

    with Session(test_engine) as db:
        assert (db.get(Book, book_id).owner_user_id is None) == is_public


def test_publish_series_preserves_media_links_and_rejects_partial_publication(account_cli, test_engine):
    with Session(test_engine) as db:
        owner = db.scalar(select(User).where(User.email.is_(None)))
        series = create_series(db, SeriesCreate(title="Demo series"))
        book = create_book(db, BookCreate(title="Demo", language="en", series_id=series.id))
        section = add_section(db, book.id)
        audio = add_audio(db, section_id=section.id)
        book.owner_user_id = series.owner_user_id = owner.id
        db.commit()
        book_id, series_id, audio_id, section_id = book.id, series.id, audio.id, section.id

    assert account_cli(["publish-demo", "--book-id", str(book_id)]) == 1

    with Session(test_engine) as db:
        assert db.get(Book, book_id).owner_user_id is not None

    assert account_cli(["publish-demo", "--series-id", str(series_id)]) == 0

    with Session(test_engine) as db:
        assert db.get(Book, book_id).owner_user_id is None
        assert db.get(Series, series_id).owner_user_id is None
        assert db.get(StoredFile, audio_id).section_id == section_id


def test_database_failure_logs_cause_without_password_or_sql_parameters(monkeypatch, capsys, caplog):
    settings = Settings(
        postgres_user="unused", postgres_password="configured-secret", postgres_db="unused", _env_file=None
    )
    monkeypatch.setattr(users, "getpass", lambda _: "sensitive-password")
    monkeypatch.setattr(users, "Settings", lambda: settings)
    monkeypatch.setattr(
        users,
        "create_database_engine",
        Mock(
            side_effect=OperationalError(
                "private SQL",
                {"password": "sensitive-password"},
                DriverOperationalError("connection lost configured-secret"),
            )
        ),
    )

    assert users.run(["create", "reader@example.com"]) == 1

    output = capsys.readouterr()
    assert "OperationalError" in caplog.text
    assert "users.py" in caplog.text
    assert "connection lost" in caplog.text
    assert "configured-secret" not in caplog.text
    assert "sensitive-password" not in caplog.text + output.out + output.err
    assert "private SQL" not in caplog.text
