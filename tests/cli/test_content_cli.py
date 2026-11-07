"""The local CLI shares validation/publication with HTTP and requires no API server."""

import json
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tests.database.content_helpers import add_audio
from voice_archive_api.cli import content_import
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.file import StoredFile
from voice_archive_api.services.books.schemas import BookCreate
from voice_archive_api.services.books.service import create_book
from voice_archive_api.services.content.schemas import ImportResult


def payload(audio_id=None):
    return {
        "schema_version": 1,
        "sections": [
            {
                "source_key": "root",
                "kind": "chapter",
                "title": "Chapter",
                "sibling_sequence": 0,
                "audio_file_id": audio_id,
            }
        ],
        "segments": [
            {
                "source_key": "text",
                "section_source_key": "root",
                "sequence": 0,
                "kind": "prose",
                "html_content": "<p><em>Text</em></p>",
                **({"narration_start_ms": 0, "narration_end_ms": 10} if audio_id else {}),
            }
        ],
    }


@pytest.fixture
def arguments(tmp_path):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(payload()), encoding="utf-8")
    return ["--book-id", "7", "--json", str(source)]


def test_calls_service_once_with_validated_content_and_disposes_engine(monkeypatch, arguments, capsys):
    engine = Mock()
    session = Mock()
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=session)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setitem(content_import.__dict__, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setitem(content_import.__dict__, "create_session_factory", Mock(return_value=factory))
    publish = Mock(return_value=ImportResult(dry_run=True, section_count=1, segment_count=1, content_version=None))
    monkeypatch.setattr(content_import.service, "publish", publish)

    assert content_import.run(arguments + ["--dry-run"]) == 0

    assert publish.call_count == 1
    assert publish.call_args.args[:2] == (session, 7)
    assert publish.call_args.args[2].content.segments[0].text_content == "Text"
    assert publish.call_args.kwargs == {"dry_run": True}
    engine.dispose.assert_called_once()
    assert json.loads(capsys.readouterr().out)["dry_run"] is True


def test_direct_database_dry_run_publish_and_conflict(test_engine, monkeypatch, arguments, capsys):
    # Override only configuration: the CLI opens its own engine and sessions.
    settings = Mock(database_url=test_engine.url)
    monkeypatch.setitem(content_import.__dict__, "Settings", lambda: settings)
    with Session(test_engine) as db:
        book_id = create_book(db, BookCreate(title="Local CLI", language="en")).id
        audio = add_audio(db)
        audio_id = audio.id
        db.commit()
    arguments[1] = str(book_id)
    source = content_import.Path(arguments[-1])
    source.write_text(json.dumps(payload(audio_id)), encoding="utf-8")

    assert content_import.run(arguments + ["--dry-run"]) == 0

    assert json.loads(capsys.readouterr().out)["content_version"] is None
    with Session(test_engine) as db:
        assert db.scalar(select(func.count()).select_from(BookSection)) == 0
        audio_file = db.get(StoredFile, audio_id)
        assert audio_file is not None
        assert audio_file.section_id is None
        book = db.get(Book, book_id)
        assert book is not None
        assert book.content_version is None

    assert content_import.run(arguments) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["content_version"] is not None
    with Session(test_engine) as db:
        assert db.scalar(select(func.count()).select_from(BookSegment)) == 1
        assert db.scalar(select(BookSegment.text_content)) == "Text"
        assert db.get(StoredFile, audio_id).section_id is not None
        assert str(db.get(Book, book_id).content_version) == result["content_version"]

    for extra in ([], ["--dry-run"]):
        assert content_import.run(arguments + extra) == 1
        assert json.loads(capsys.readouterr().err)["error"]["code"] == "content_exists"

    arguments[1] = "9223372036854775807"

    assert content_import.run(arguments) == 1

    assert json.loads(capsys.readouterr().err)["error"]["code"] == "book_not_found"


def test_invalid_inputs_do_not_create_database_engine(monkeypatch, arguments, capsys):
    engine = Mock()
    monkeypatch.setitem(content_import.__dict__, "create_database_engine", engine)
    source = content_import.Path(arguments[-1])

    for text in ("not JSON", '{"schema_version":1,"schema_version":1}', '{"schema_version":1}'):
        source.write_text(text, encoding="utf-8")

        assert content_import.run(arguments) == 1

        assert json.loads(capsys.readouterr().err)["error"]["code"] == "content_validation_error"

    source.write_bytes(b"x" * (content_import.MAX_PAYLOAD_BYTES + 1))

    assert content_import.run(arguments) == 1

    assert json.loads(capsys.readouterr().err)["issues"][0]["code"] == "payload_limit"

    arguments[-1] += ".missing"

    assert content_import.run(arguments) == 1

    assert "Cannot read input" in capsys.readouterr().err
    engine.assert_not_called()


def test_missing_configuration_is_reported_without_secrets(monkeypatch, arguments, capsys):
    for name in list(content_import.Settings.model_fields):
        monkeypatch.delenv("VOICE_ARCHIVE_" + name.upper(), raising=False)
    monkeypatch.setenv("VOICE_ARCHIVE_POSTGRES_PASSWORD", "private-secret")

    assert content_import.run(arguments) == 1

    output = capsys.readouterr().err
    assert "Invalid configuration" in output
    assert "private-secret" not in output


@pytest.mark.parametrize("dry_run", [False, True])
def test_database_failure_is_redacted_and_never_retried(monkeypatch, arguments, capsys, dry_run):
    create_engine = Mock(side_effect=OperationalError("password=private-secret", {}, Exception("private-secret")))
    monkeypatch.setitem(content_import.__dict__, "create_database_engine", create_engine)

    assert content_import.run(arguments + (["--dry-run"] if dry_run else [])) == (1 if dry_run else 3)

    create_engine.assert_called_once()
    output = capsys.readouterr().err
    assert "private-secret" not in output
    assert "no automatic retry" in output


@pytest.mark.parametrize("book_id", ["0", "-1", "9223372036854775808"])
def test_invalid_book_id(arguments, book_id):
    with pytest.raises(SystemExit) as exc:
        content_import.run(arguments + ["--book-id", book_id])

    assert exc.value.code == 2
