import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
from google.genai import Client
from pydantic import SecretStr
from pytest_bdd import given, parsers, scenarios, then, when
from sqlalchemy import select, text

from voice_archive_api.cli import embeddings as cli
from voice_archive_api.db.models.batch_queue import BookBatchJob
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSegment
from voice_archive_api.db.models.embedding_batch import EmbeddingBatchJob, EmbeddingBatchRequest
from voice_archive_api.db.models.windows import RetrievalWindow, WindowEmbedding
from voice_archive_api.logic.windows import compute_embedding_input_hash
from voice_archive_api.services.books.service import BookNotFound
from voice_archive_api.services.embeddings import submit_batches
from voice_archive_api.services.embeddings.collect_results import collect_embedding_batches
from voice_archive_api.services.embeddings.service import run_embedding_maintenance
from voice_archive_api.services.embeddings.submit_batches import (
    enqueue_book_embeddings,
    submit_next_embedding_batch,
)
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
        if request.method == "GET":
            state.setdefault("reads", []).append(str(request.url))
            if request.url.path.endswith("/batches"):
                if state.get("listing") == "failure" and "pageToken" in request.url.params:
                    return httpx.Response(503, json={"error": {"message": "batch-secret"}})
                if "pageToken" not in request.url.params:
                    return httpx.Response(200, json={"operations": [], "nextPageToken": "last"})
                return httpx.Response(200, json={"operations": state.get("listed_jobs", [])})
            if ":download" in request.url.path:
                return httpx.Response(200, content=state["results"])
            return httpx.Response(
                200,
                json={
                    "name": "batches/accepted-1",
                    "metadata": {
                        "state": state.get("remote_state", "BATCH_STATE_RUNNING"),
                        "output": {"responsesFile": "files/output"},
                    },
                },
            )
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
            return httpx.Response(
                403,
                json={
                    "error": {
                        "status": "PERMISSION_DENIED",
                        "message": "Batch embeddings are disabled for this project (key=batch-secret)",
                    }
                },
            )
        if state["mode"] == "rebuild":
            rebuild_book_windows(factory, settings, book_id, len)
        return httpx.Response(200, json={"name": f"batches/accepted-{len(state['submissions'])}"})

    def create_test_client(*, api_key, http_options):
        assert api_key == "batch-secret"
        assert http_options.retry_options.attempts == 1
        http_options.client_args = {"transport": httpx.MockTransport(respond)}
        return Client(api_key=api_key, http_options=http_options)

    monkeypatch.setattr("voice_archive_api.services.embeddings.submit_batches.genai.Client", create_test_client)
    monkeypatch.setitem(cli.__dict__, "Settings", lambda: settings)
    monkeypatch.setattr(
        "voice_archive_api.services.embeddings.submit_batches.LocalTokenizer",
        lambda model: SimpleNamespace(count_tokens=lambda text: SimpleNamespace(total_tokens=len(text))),
    )

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
def verify_released_claims(client, batch_case, caplog):
    assert batch_case["exit_code"] == 1
    assert "batch-secret" not in batch_case["output"].err
    assert "batch-secret" not in caplog.text
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
    assert json.loads(batch_case["output"].out)["state"] == "failed"
    assert batch_case["uploads"] == batch_case["submissions"] == []
    with client.app.state.session_factory() as db:
        assert not db.scalars(select(EmbeddingBatchJob)).all()
        assert not db.scalars(select(EmbeddingBatchRequest)).all()


@given(parsers.parse("its embedding input is {problem}"))
def invalidate_book_embedding_input(client, problem):
    settings = client.app.state.settings
    settings.embedding_batch_tokens = 100
    with client.app.state.session_factory.begin() as db:
        embedding = db.scalar(select(WindowEmbedding).where(WindowEmbedding.kind == "primary"))
        if problem == "stale":
            embedding.input_hash = "stale"
        else:
            window = db.get(RetrievalWindow, embedding.window_id)
            window.primary_text = "x" * 200
            embedding.input_hash = compute_embedding_input_hash(
                window.path,
                window.primary_text,
                model=embedding.model,
                dimensions=embedding.dimensions,
                input_format=embedding.input_format,
            )


@given("a healthy book is queued after this book")
def enqueue_healthy_book(client, batch_case):
    factory, settings = client.app.state.session_factory, client.app.state.settings
    enqueue_book_embeddings(factory, batch_case["book_id"])
    healthy_id = client.post("/api/v1/books", json={"title": "Healthy", "language": "en"}).json()["id"]
    response = client.post(
        f"/api/v1/books/{healthy_id}/content-imports",
        json={
            "schema_version": 1,
            "sections": [],
            "segments": [{"source_key": "a", "sequence": 0, "kind": "prose", "html_content": "<p>OK</p>"}],
        },
    )
    assert response.status_code == 200
    rebuild_book_windows(factory, settings, healthy_id, len)
    enqueue_book_embeddings(factory, healthy_id)
    batch_case["healthy_id"] = healthy_id


@then("only the invalid book fails and the healthy book is submitted")
def verify_isolated_book_failure(client, batch_case):
    assert len(batch_case["submissions"]) == 1
    with client.app.state.session_factory() as db:
        assert db.get(BookBatchJob, (batch_case["book_id"], "embeddings")).state == "failed"
        assert db.get(BookBatchJob, (batch_case["healthy_id"], "embeddings")).state == "running"
        batch = db.scalars(select(EmbeddingBatchJob)).one()
        assert batch.book_id == batch_case["healthy_id"]
        assert db.scalars(select(EmbeddingBatchRequest.batch_id)).all() == [batch.id]


@given("the first book disappears while being locked")
def remove_book_during_lock(batch_case, monkeypatch):
    lock_book = submit_batches.lock_book_for_series_write

    def lock_available_book(db, book_id, changes):
        if book_id == batch_case["book_id"]:
            raise BookNotFound
        return lock_book(db, book_id, changes)

    monkeypatch.setattr(submit_batches, "lock_book_for_series_write", lock_available_book)


@given("the token budget fits one request")
def limit_embedding_batch(client):
    client.app.state.settings.embedding_batch_tokens = 100


@when("maintenance completes the current batch")
def complete_current_batch(client, batch_case):
    batch_case["remote_state"] = "BATCH_STATE_SUCCEEDED"
    batch_case["results"] = "\n".join(
        json.dumps(
            {"key": row["key"], "response": {"embedding": {"values": [0.5] * row["request"]["output_dimensionality"]}}}
        )
        for row in batch_case["uploads"][-1]
    ).encode()

    failures = run_embedding_maintenance(client.app.state.session_factory, client.app.state.settings)

    assert failures == 0


@when("maintenance advances the queue")
def advance_embedding_queue(client):
    failures = run_embedding_maintenance(client.app.state.session_factory, client.app.state.settings)

    assert failures == 0


@then("only the failed request gets another attempt")
def verify_failed_request_retry(client, batch_case):
    assert [len(rows) for rows in batch_case["uploads"]] == [2, 1]
    failed_key = UUID(json.loads(batch_case["results"].splitlines()[0])["key"])
    retry_key = UUID(batch_case["uploads"][1][0]["key"])
    with client.app.state.session_factory() as db:
        assert (
            db.get(EmbeddingBatchRequest, failed_key).embedding_id
            == db.get(EmbeddingBatchRequest, retry_key).embedding_id
        )


@then("one request is active and the other is still pending")
def verify_bounded_claim(client, batch_case):
    assert len(batch_case["uploads"]) == 1
    assert len(batch_case["uploads"][0]) == 1
    with client.app.state.session_factory() as db:
        assert len(db.scalars(select(EmbeddingBatchRequest)).all()) == 1
        assert db.get(BookBatchJob, (batch_case["book_id"], "embeddings")).state == "running"


@then("the book finishes automatically after two bounded batches")
def verify_finished_queue(client, batch_case):
    assert [len(rows) for rows in batch_case["uploads"]] == [1, 1]
    with client.app.state.session_factory() as db:
        assert db.get(BookBatchJob, (batch_case["book_id"], "embeddings")).state == "completed"
        assert all(row.vector is not None for row in db.scalars(select(WindowEmbedding)))
        assert not any(db.scalars(select(EmbeddingBatchRequest.active)))


@when("maintenance retries until the attempt limit")
def exhaust_submission_attempts(client, batch_case):
    for _ in range(client.app.state.settings.embedding_max_attempts):
        run_embedding_maintenance(client.app.state.session_factory, client.app.state.settings)


@then("automatic retries stop and the book is failed")
def verify_retry_limit(client, batch_case):
    assert len(batch_case["submissions"]) == client.app.state.settings.embedding_max_attempts
    with client.app.state.session_factory() as db:
        assert db.get(BookBatchJob, (batch_case["book_id"], "embeddings")).state == "failed"

    assert submit_next_embedding_batch(client.app.state.session_factory, client.app.state.settings) == []


@given("another operation owns the global batch slot")
def occupy_shared_slot(client, batch_case):
    with client.app.state.session_factory.begin() as db:
        db.add(BookBatchJob(book_id=batch_case["book_id"], kind="summaries", state="running", active_batch_id=uuid4()))


@then("embedding work stays queued without any provider submission")
def verify_shared_slot_blocks_embeddings(client, batch_case):
    assert batch_case["uploads"] == batch_case["submissions"] == []
    with client.app.state.session_factory() as db:
        assert db.get(BookBatchJob, (batch_case["book_id"], "embeddings")).state == "queued"


@given("its submitted batch returns results", target_fixture="collection_results")
def prepare_collection(client, batch_case, capsys):
    submit_embeddings(batch_case, capsys)
    batch_case["remote_state"] = "BATCH_STATE_SUCCEEDED"
    results = [
        {
            "key": row["key"],
            "response": {"embedding": {"values": [0.5 + index] * row["request"]["output_dimensionality"]}},
        }
        for index, row in enumerate(reversed(batch_case["uploads"][0]))
    ]
    batch_case["expected_vectors"] = {UUID(row["key"]): row["response"]["embedding"]["values"] for row in results}
    return results


@given(parsers.parse("the collection case is {case}"))
def configure_collection_case(client, batch_case, collection_results, case):
    factory = client.app.state.session_factory
    if case == "request-error":
        collection_results[0] = {"key": collection_results[0]["key"], "error": {"code": 3, "message": "invalid"}}
    elif case == "changed":
        with factory.begin() as db:
            db.scalar(select(WindowEmbedding)).input_hash = "changed"
    elif case == "deleted":
        rebuild_book_windows(factory, client.app.state.settings, batch_case["book_id"], len)
    elif case == "duplicate":
        collection_results.append(collection_results[0])
    elif case == "missing":
        collection_results.pop()
    elif case == "unknown":
        collection_results[0]["key"] = str(uuid4())
    elif case == "dimensions":
        collection_results[0]["response"]["embedding"]["values"] = [0.5]
    elif case == "nonfinite":
        collection_results[0]["response"]["embedding"]["values"][0] = float("inf")
    elif case == "boolean":
        collection_results[0]["response"]["embedding"]["values"][0] = True
    elif case == "zero":
        collection_results[0]["response"]["embedding"]["values"] = [0.0] * 768
    elif case in ("running", "failed", "cancelled", "expired"):
        batch_case["remote_state"] = f"BATCH_STATE_{case.upper()}"
    batch_case["results"] = "\n".join(json.dumps(row) for row in collection_results).encode()
    if case in ("empty", "malformed"):
        batch_case["results"] = b"" if case == "empty" else b"not json"


@when("maintenance collects twice")
def collect_twice(client, batch_case, caplog):
    factory, settings = client.app.state.session_factory, client.app.state.settings

    batch_case["collection_failures"] = collect_embedding_batches(factory, settings)
    batch_case["repeat_failures"] = collect_embedding_batches(factory, settings)


@then(parsers.parse("the batch is {state} with {vectors} vectors and {failures} collection failures"))
def verify_collection(client, batch_case, state, vectors, failures):
    assert batch_case["collection_failures"] == batch_case["repeat_failures"] == int(failures)
    with client.app.state.session_factory() as db:
        assert db.scalar(select(EmbeddingBatchJob.state)) == state
        stored = db.execute(
            select(WindowEmbedding, EmbeddingBatchRequest.id)
            .join(EmbeddingBatchRequest, EmbeddingBatchRequest.embedding_id == WindowEmbedding.id)
            .where(WindowEmbedding.vector.is_not(None))
        ).all()
        assert len(stored) == int(vectors)
        for embedding, request_id in stored:
            assert embedding.vector == batch_case["expected_vectors"][request_id]
        assert all(request.active == (state == "submitted") for request in db.scalars(select(EmbeddingBatchRequest)))
    if state.startswith("completed"):
        assert sum(":download" in url for url in batch_case["reads"]) == 1


@given(parsers.parse("an interrupted submission is {age} and its provider listing is {listing}"))
def prepare_reconciliation(client, batch_case, capsys, age, listing):
    batch_case["mode"] = "timeout"
    submit_embeddings(batch_case, capsys)
    batch_case["listing"] = listing
    with client.app.state.session_factory.begin() as db:
        batch = db.scalar(select(EmbeddingBatchJob))
        batch.created_at = datetime.now(UTC) - timedelta(hours=1) if age == "old" else datetime.now(UTC)
        batch_case["listed_jobs"] = (
            [{"name": "batches/recovered", "metadata": {"displayName": str(batch.id)}}] if listing == "matched" else []
        )


@then(parsers.parse("its reconciliation state is {state} and claims are {active}"))
def verify_reconciliation(client, batch_case, state, active):
    with client.app.state.session_factory() as db:
        batch = db.scalar(select(EmbeddingBatchJob))
        assert batch.state == state
        assert all(request.active == (active == "yes") for request in db.scalars(select(EmbeddingBatchRequest)))
        if state == "submitted":
            assert batch.provider_name == "batches/recovered"
