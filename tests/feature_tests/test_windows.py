import json
from types import SimpleNamespace

import pytest
from google.genai.types import CountTokensResult
from pytest_bdd import given, scenarios, then, when
from sentencepiece import SentencePieceProcessor, SentencePieceTrainer
from sqlalchemy import select

from voice_archive_api.cli import windows as cli
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSegment
from voice_archive_api.db.models.windows import RetrievalWindow, WindowEmbedding
from voice_archive_api.logic.windows import compute_embedding_input_hash

scenarios("../features/windows/rebuild.feature")


@pytest.fixture(name="window_case")
def create_window_case(client, summary_book, tmp_path, monkeypatch):
    book_id, version = summary_book
    prefix = tmp_path / "tokenizer"
    SentencePieceTrainer.train(
        sentence_iterator=iter(["title: Journey Home | text: He came home to see his mother. ತಾಯಿಯನ್ನು ನೋಡಲು ಮನೆಗೆ ಬಂದನು."]),
        model_prefix=str(prefix),
        model_type="char",
        vocab_size=100,
        hard_vocab_limit=False,
        minloglevel=2,
    )
    processor = SentencePieceProcessor(model_file=str(prefix.with_suffix(".model")))

    def create_local_tokenizer(model_name):
        assert model_name == "gemini-2.5-flash"
        return SimpleNamespace(count_tokens=lambda text: CountTokensResult(total_tokens=len(processor.encode(text))))

    monkeypatch.setitem(cli.__dict__, "LocalTokenizer", create_local_tokenizer)
    monkeypatch.setitem(cli.__dict__, "Settings", lambda: client.app.state.settings)
    client.app.state.settings.gemini_embedding_model = "configured-embedding-model"
    client.app.state.settings.gemini_embedding_dimensions = 16
    client.app.state.settings.embedding_input_format = "configured-document-format"

    return {"book_id": book_id, "version": version}


def prepare_source(client, state, language):
    with client.app.state.session_factory.begin() as db:
        db.get(Book, state["book_id"]).language = language
        passage = db.scalar(select(BookSegment))
        passage.text_content = (
            "ತಾಯಿಯನ್ನು ನೋಡಲು ಮನೆಗೆ ಬಂದನು. " * 35 if language == "kn-IN" else "He came home to see his mother."
        )
        passage.html_content = f"<p>{passage.text_content}</p>"
        passage.english_translation = "He came home to see his mother. " * 20 if language == "kn-IN" else None
        state["source"] = (passage.id, passage.text_content, passage.html_content, passage.english_translation)


@given("an English book is ready for windowing")
def prepare_english_source(client, window_case):
    prepare_source(client, window_case, "en-US")


@given("a Kannada book has a long passage and an accepted translation")
def prepare_kannada_source(client, window_case):
    prepare_source(client, window_case, "kn-IN")


def run_window_command(state, capsys, operation="rebuild", extra=()):
    state["exit_code"] = cli.run([operation, "--book-id", str(state["book_id"]), *extra])
    state["output"] = capsys.readouterr()


@given("its windows already exist")
@when("I rebuild its windows with the local command")
def rebuild_windows(client, window_case, capsys):
    run_window_command(window_case, capsys)
    assert window_case["exit_code"] == 0, window_case["output"].err
    with client.app.state.session_factory() as db:
        window_case["window_ids"] = set(db.scalars(select(RetrievalWindow.id)))


@given("its planned embeddings have completed vectors")
def complete_embedding_vectors(client, window_case):
    with client.app.state.session_factory.begin() as db:
        window_case["previous_window_ids"] = set(db.scalars(select(RetrievalWindow.id)))
        window_case["previous_embedding_ids"] = set(db.scalars(select(WindowEmbedding.id)))
        for embedding in db.scalars(select(WindowEmbedding)):
            embedding.vector = [1.0] * embedding.dimensions


@when("I dry-run its windows with the local command")
def dry_run_windows(window_case, capsys):
    run_window_command(window_case, capsys, "dry-run")


@when("I rebuild with a budget too small for its complete passage")
def rebuild_oversized_windows(window_case, capsys):
    run_window_command(window_case, capsys, extra=["--window-tokens", "32", "--overlap-tokens", "0"])


@when("I clear its windows with the local command")
def clear_windows(client, window_case, capsys):
    exit_code = cli.run(["clear", "--book-id", str(window_case["book_id"])])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["cleared"]
    assert_canonical_content_unchanged(client, window_case)


@when("I purge its canonical content")
def purge_canonical_content(client, window_case):
    response = client.delete(
        f"/api/v1/books/{window_case['book_id']}/content",
        headers={"If-Match": f'"book-content-v1-{window_case["book_id"]}-content-{window_case["version"]}"'},
    )

    assert response.status_code == 204, response.text


@when("I delete the book")
def delete_book(client, window_case):
    response = client.delete(f"/api/v1/books/{window_case['book_id']}")

    assert response.status_code == 204, response.text


def assert_canonical_content_unchanged(client, state):
    with client.app.state.session_factory() as db:
        passage = db.get(BookSegment, state["source"][0])
        assert (passage.id, passage.text_content, passage.html_content, passage.english_translation) == state["source"]
        assert db.get(Book, state["book_id"]).content_version == state["version"]


@then("stored windows have the assembled text and empty planned embeddings")
def verify_stored_windows(client, window_case):
    assert json.loads(window_case["output"].out)["window_count"] == 1

    with client.app.state.session_factory() as db:
        window = db.scalar(select(RetrievalWindow))
        embeddings = db.scalars(select(WindowEmbedding).order_by(WindowEmbedding.kind)).all()

        assert window.segment_ids == [window_case["source"][0]]
        assert window.path == "Journey > Home"
        assert window.content_version == window_case["version"]
        assert window.primary_text == window_case["source"][1]
        assert window.translated_text == window_case["source"][3]
        assert [row.kind for row in embeddings] == (
            ["primary", "translated"] if window.translated_text else ["primary"]
        )
        assert all(row.vector is None for row in embeddings)
        assert all(row.model == "configured-embedding-model" and row.dimensions == 16 for row in embeddings)
        assert all(row.input_format == "configured-document-format" for row in embeddings)
        for embedding in embeddings:
            text = window.primary_text if embedding.kind == "primary" else window.translated_text
            assert embedding.input_hash == compute_embedding_input_hash(
                window.path,
                text,
                model="configured-embedding-model",
                dimensions=16,
                input_format="configured-document-format",
            )

    assert_canonical_content_unchanged(client, window_case)


@then("assembled windows are reported without storing any rows")
def verify_dry_run(client, window_case):
    assert window_case["exit_code"] == 0
    window = json.loads(window_case["output"].out)["windows"][0]
    assert window["primary_text"] == window_case["source"][1]
    with client.app.state.session_factory() as db:
        assert not db.scalars(select(RetrievalWindow)).all()
        assert not db.scalars(select(WindowEmbedding)).all()


@then("the command reports the oversized passage and preserves existing windows")
def verify_rejected_rebuild(client, window_case):
    assert window_case["exit_code"] == 1
    assert "Segment" in window_case["output"].err and "budget 32" in window_case["output"].err
    with client.app.state.session_factory() as db:
        assert set(db.scalars(select(RetrievalWindow.id))) == window_case["window_ids"]
    assert_canonical_content_unchanged(client, window_case)


@then("fresh windows and empty embeddings replace the previous rows")
def verify_replaced_windows(client, window_case):
    with client.app.state.session_factory() as db:
        window_ids = set(db.scalars(select(RetrievalWindow.id)))
        embeddings = db.scalars(select(WindowEmbedding)).all()

        assert window_ids and window_ids.isdisjoint(window_case["previous_window_ids"])
        assert embeddings and {row.id for row in embeddings}.isdisjoint(window_case["previous_embedding_ids"])
        assert all(row.vector is None for row in embeddings)

    assert_canonical_content_unchanged(client, window_case)


@then("no windows or planned embeddings remain")
def verify_removed_windows(client):
    with client.app.state.session_factory() as db:
        assert not db.scalars(select(RetrievalWindow)).all()
        assert not db.scalars(select(WindowEmbedding)).all()
