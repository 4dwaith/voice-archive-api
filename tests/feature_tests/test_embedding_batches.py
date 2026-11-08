import json
from uuid import UUID, uuid4

import httpx
from google.genai import Client
from pydantic import SecretStr
from pytest_bdd import given, scenarios, then, when
from sqlalchemy import select, text

from voice_archive_api.cli import embeddings as cli
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSegment
from voice_archive_api.db.models.embedding_batch import EmbeddingBatchJob, EmbeddingBatchRequest
from voice_archive_api.db.models.windows import RetrievalWindow, WindowEmbedding
from voice_archive_api.logic.windows import compute_embedding_input_hash
from voice_archive_api.services.windows import rebuild_book_windows

scenarios("../features/windows/embedding_batches.feature")


@given("a book has primary and translated windows ready for embedding", target_fixture="batch_case")
def prepare_embedding_book(client, summary_book, monkeypatch):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    settings = client.app.state.settings
    settings.gemini_batch_api_key = SecretStr("batch-secret")

    with factory.begin() as db:
        db.scalar(select(BookSegment)).english_translation = "He came home."

    rebuild_book_windows(factory, settings, book_id, len)

    state = {"book_id": book_id, "mode": "success", "uploads": [], "submissions": []}

    def respond(request):
        if request.url.path == "/upload/v1beta/files":
            if state["mode"] == "upload-failure":
                return httpx.Response(403, json={"error": {"message": "batch-secret"}})
            return httpx.Response(200, headers={"x-goog-upload-url": "https://upload.test/input"}, json={})
        if request.url.host == "upload.test":
            # The upload must contain actual UTF-8 rather than expanded Unicode escapes.
            assert b"\\u" not in request.content
            state["uploads"].append([json.loads(line) for line in request.content.decode().splitlines()])
            return httpx.Response(
                200,
                headers={"x-goog-upload-status": "final"},
                json={"file": {"name": f"files/input-{len(state['uploads'])}"}},
            )
        assert request.url.path.endswith(":asyncBatchEmbedContent")
        payload = json.loads(request.content)
        state["submissions"].append(payload)
        with factory.begin() as db:
            db.execute(text("SET LOCAL lock_timeout = '500ms'"))
            assert db.get(Book, book_id, with_for_update=True) is not None
        if state["mode"] == "timeout":
            raise httpx.ReadTimeout("batch-secret", request=request)
        if state["mode"] == "rejection":
            return httpx.Response(403, json={"error": {"message": "batch-secret"}})
        if state["mode"] == "rebuild":
            rebuild_book_windows(factory, settings, book_id, len)
        return httpx.Response(200, json={"name": f"batches/accepted-{len(state['submissions'])}"})

    def create_test_client(*, api_key, http_options):
        assert api_key == "batch-secret"
        assert http_options.retry_options.attempts == 1
        http_options.client_args = {"transport": httpx.MockTransport(respond)}
        return Client(api_key=api_key, http_options=http_options)

    monkeypatch.setattr("voice_archive_api.services.embedding_batches.genai.Client", create_test_client)
    monkeypatch.setitem(cli.__dict__, "Settings", lambda: settings)

    return state


@given("its primary embedding already has a vector")
def complete_primary_vector(client):
    with client.app.state.session_factory.begin() as db:
        row = db.scalar(select(WindowEmbedding).where(WindowEmbedding.kind == "primary"))
        row.vector = [1.0] * row.dimensions


@given("its window has additional model and dimension profiles")
def add_embedding_profile(client):
    with client.app.state.session_factory.begin() as db:
        window = db.scalar(select(RetrievalWindow))
        for model in ("another-embedding-model", "gemini-embedding-2"):
            db.add(
                WindowEmbedding(
                    id=uuid4(),
                    window_id=window.id,
                    kind="primary",
                    model=model,
                    dimensions=16,
                    input_format="another-format-identity",
                    input_hash=compute_embedding_input_hash(
                        window.path,
                        window.primary_text,
                        model=model,
                        dimensions=16,
                        input_format="another-format-identity",
                    ),
                    vector=None,
                )
            )


@given("the provider rejects batch creation")
def reject_submission(batch_case):
    batch_case["mode"] = "rejection"


@given("the batch input upload fails")
def fail_upload(batch_case):
    batch_case["mode"] = "upload-failure"


@given("the batch creation response is lost")
def lose_submission_response(batch_case):
    batch_case["mode"] = "timeout"


@given("its windows are rebuilt while the batch is submitted")
def rebuild_during_submission(batch_case):
    batch_case["mode"] = "rebuild"


@given("its stored input hash is stale")
def invalidate_input_hash(client):
    with client.app.state.session_factory.begin() as db:
        db.scalar(select(WindowEmbedding)).input_hash = "stale"


@when("I submit its embeddings")
def submit_embeddings(batch_case, capsys):
    batch_case["exit_code"] = cli.run(["--book-id", str(batch_case["book_id"])])
    batch_case["output"] = capsys.readouterr()


@when("I submit its embeddings twice")
def submit_embeddings_twice(batch_case, capsys):
    submit_embeddings(batch_case, capsys)
    batch_case["first_exit_code"] = batch_case["exit_code"]
    batch_case["first_output"] = batch_case["output"]

    submit_embeddings(batch_case, capsys)


@when("the provider recovers and I submit its embeddings")
def retry_submission(batch_case, capsys):
    batch_case["mode"] = "success"

    submit_embeddings(batch_case, capsys)


@then("only its missing translated vector is requested once")
def verify_missing_vector_submission(client, batch_case):
    assert batch_case["first_exit_code"] == batch_case["exit_code"] == 0
    assert json.loads(batch_case["output"].out)["batches"] == []
    assert len(batch_case["submissions"]) == 1
    payload = batch_case["submissions"][0]["batch"]
    assert payload["inputConfig"] == {"file_name": "files/input-1"}
    (line,) = batch_case["uploads"][0]
    assert line["request"] == {
        "content": {"parts": [{"text": "title: Journey > Home | text: He came home."}]},
        "output_dimensionality": 768,
    }
    with client.app.state.session_factory() as db:
        batch = db.scalars(select(EmbeddingBatchJob)).one()
        mapping = db.get(EmbeddingBatchRequest, UUID(line["key"]))
        embedding = db.get(WindowEmbedding, mapping.embedding_id)

        assert (batch.state, batch.provider_name, batch.input_file_name) == (
            "submitted",
            "batches/accepted-1",
            "files/input-1",
        )
        assert str(batch.id) == payload["displayName"]
        assert mapping.batch_id == batch.id and mapping.active
        assert mapping.input_hash == embedding.input_hash
        assert embedding.kind == "translated" and embedding.vector is None


@then("its failed upload claims are released")
@then("its rejected submission claims are released")
def verify_released_claims(client, batch_case):
    assert batch_case["exit_code"] == 1
    assert "batch-secret" not in batch_case["output"].err
    with client.app.state.session_factory() as db:
        assert db.scalars(select(EmbeddingBatchJob.state)).all() == ["failed"]
        assert db.scalars(select(EmbeddingBatchRequest.active)).all() == [False, False]


@then("a new batch is submitted")
def verify_retried_submission(client, batch_case):
    assert batch_case["exit_code"] == 0
    with client.app.state.session_factory() as db:
        assert len(db.scalars(select(EmbeddingBatchJob).where(EmbeddingBatchJob.state == "submitted")).all()) == 1
        assert len(db.scalars(select(EmbeddingBatchRequest).where(EmbeddingBatchRequest.active)).all()) == 2


@then("each model receives a keyed file preserving its request dimensions")
def verify_multiple_profiles(client, batch_case):
    assert batch_case["exit_code"] == 0
    assert [len(rows) for rows in batch_case["uploads"]] == [1, 3]
    assert [{row["request"]["output_dimensionality"] for row in rows} for rows in batch_case["uploads"]] == [
        {16},
        {16, 768},
    ]
    with client.app.state.session_factory() as db:
        assert set(db.scalars(select(EmbeddingBatchJob.model))) == {"another-embedding-model", "gemini-embedding-2"}
        for payload, rows in zip(batch_case["submissions"], batch_case["uploads"], strict=True):
            batch_id = UUID(payload["batch"]["displayName"])
            batch = db.get(EmbeddingBatchJob, batch_id)
            assert payload["batch"]["inputConfig"] == {"file_name": batch.input_file_name}
            for row in rows:
                mapping = db.get(EmbeddingBatchRequest, UUID(row["key"]))
                embedding = db.get(WindowEmbedding, mapping.embedding_id)
                window = db.get(RetrievalWindow, embedding.window_id)
                source = window.primary_text if embedding.kind == "primary" else window.translated_text
                assert row["request"]["content"]["parts"] == [{"text": f"title: {window.path} | text: {source}"}]
                assert row["request"]["output_dimensionality"] == embedding.dimensions
                assert mapping.batch_id == batch_id
                assert mapping.input_hash == embedding.input_hash


@then("its uncertain submission remains claimed without another provider request")
def verify_uncertain_submission(client, batch_case):
    assert batch_case["first_exit_code"] == 1 and batch_case["exit_code"] == 0
    assert "claims retained" in batch_case["first_output"].err
    assert "batch-secret" not in batch_case["first_output"].err
    assert len(batch_case["submissions"]) == 1
    with client.app.state.session_factory() as db:
        assert db.scalars(select(EmbeddingBatchJob.state)).all() == ["submitting"]
        assert db.scalars(select(EmbeddingBatchRequest.active)).all() == [True, True]


@then("the submitted identities survive and the replacement vectors stay empty")
def verify_replaced_targets(client, batch_case):
    assert batch_case["exit_code"] == 0
    with client.app.state.session_factory() as db:
        submitted_ids = set(db.scalars(select(EmbeddingBatchRequest.embedding_id)))
        current = db.scalars(select(WindowEmbedding)).all()

        assert len(submitted_ids) == len(current) == 2
        assert submitted_ids.isdisjoint(row.id for row in current)
        assert all(row.vector is None for row in current)


@then("no batch is stored or sent")
def verify_invalid_input_rejection(client, batch_case):
    assert batch_case["exit_code"] == 1
    assert "hash does not match" in batch_case["output"].err
    assert batch_case["uploads"] == batch_case["submissions"] == []
    with client.app.state.session_factory() as db:
        assert not db.scalars(select(EmbeddingBatchJob)).all()
        assert not db.scalars(select(EmbeddingBatchRequest)).all()
