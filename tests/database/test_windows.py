"""Transaction rollback and foreign-key cascading for retrieval windows."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, func, select
from sqlalchemy.orm import sessionmaker

from voice_archive_api.db.models.batch_queue import BookBatchJob
from voice_archive_api.db.models.content import BookSegment
from voice_archive_api.db.models.embedding_batch import EmbeddingBatchJob, EmbeddingBatchRequest
from voice_archive_api.db.models.windows import RetrievalWindow, WindowEmbedding
from voice_archive_api.logic.windows import compute_embedding_input_hash
from voice_archive_api.services.embeddings.collect_results import publish_embedding_results
from voice_archive_api.services.embeddings.submit_batches import (
    create_embedding_batch_request_input,
    enqueue_book_embeddings,
)
from voice_archive_api.services.windows import rebuild_book_windows


@pytest.fixture(name="window_book")
def create_window_book(client, summary_book):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    with factory.begin() as db:
        db.scalar(select(BookSegment)).english_translation = "He came home to see his mother."

    return factory, client.app.state.settings, book_id


def test_failed_rebuild_preserves_existing_rows_and_vectors(client, window_book):
    factory, settings, book_id = window_book
    rebuild_book_windows(factory, settings, book_id, len)
    with factory.begin() as db:
        original_window_ids = set(db.scalars(select(RetrievalWindow.id)))
        original_ids = set(db.scalars(select(WindowEmbedding.id)))
        for embedding in db.scalars(select(WindowEmbedding)):
            embedding.vector = [1.0] * embedding.dimensions

    def interrupt_embedding_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO window_embeddings"):
            raise RuntimeError("Interrupted publication")

    event.listen(client.app.state.engine, "before_cursor_execute", interrupt_embedding_insert)
    try:
        with pytest.raises(RuntimeError, match="Interrupted publication"):
            rebuild_book_windows(factory, settings, book_id, len)
    finally:
        event.remove(client.app.state.engine, "before_cursor_execute", interrupt_embedding_insert)

    with factory() as db:
        embeddings = db.scalars(select(WindowEmbedding)).all()

        assert set(db.scalars(select(RetrievalWindow.id))) == original_window_ids
        assert {row.id for row in embeddings} == original_ids
        assert all(row.vector == [1.0] * row.dimensions for row in embeddings)


def test_deleting_windows_cascades_to_embeddings(window_book):
    factory, settings, book_id = window_book
    rebuild_book_windows(factory, settings, book_id, len)
    with factory() as db:
        assert db.scalar(select(WindowEmbedding.id)) is not None

    with factory.begin() as db:
        db.execute(delete(RetrievalWindow).where(RetrievalWindow.book_id == book_id))

    with factory() as db:
        assert not db.scalars(select(WindowEmbedding)).all()


def test_concurrent_books_cannot_claim_two_global_batches(client, window_book):
    factory, settings, book_id = window_book
    rebuild_book_windows(factory, settings, book_id, len)

    enqueue_book_embeddings(factory, book_id)

    other_id = client.post("/api/v1/books", json={"title": "Other", "language": "en"}).json()["id"]
    with factory.begin() as db:
        window = RetrievalWindow(
            id=uuid4(),
            book_id=other_id,
            content_version=uuid4(),
            sequence=0,
            section_id=None,
            segment_ids=[],
            path="Other",
            primary_text="Another book.",
            translated_text=None,
        )
        db.add(window)
        db.flush()
        db.add(
            WindowEmbedding(
                id=uuid4(),
                window_id=window.id,
                kind="primary",
                model=settings.gemini_embedding_model,
                dimensions=768,
                input_format=settings.embedding_input_format,
                input_hash=compute_embedding_input_hash(
                    window.path,
                    window.primary_text,
                    model=settings.gemini_embedding_model,
                    dimensions=768,
                    input_format=settings.embedding_input_format,
                ),
                vector=None,
            )
        )
    enqueue_book_embeddings(factory, other_id)

    with ThreadPoolExecutor(max_workers=2) as workers:
        claims = list(
            workers.map(
                lambda _: create_embedding_batch_request_input(factory, settings, len),
                range(2),
            )
        )

    assert sum(claim is not None for claim in claims) == 1


def test_an_earlier_transaction_cannot_hide_a_submission_from_retry_counts(window_book):
    factory, settings, book_id = window_book
    rebuild_book_windows(factory, settings, book_id, len)

    with factory.begin() as db:
        transaction_started = db.scalar(select(func.now()))
        enqueue_book_embeddings(factory, book_id)
        claim_factory = sessionmaker(bind=db.connection())

        batch_id, _, _, _, _ = create_embedding_batch_request_input(claim_factory, settings, len)

        job = db.get(BookBatchJob, (book_id, "embeddings"))
        batch = db.get(EmbeddingBatchJob, batch_id)
        assert transaction_started < job.requested_at <= batch.created_at


def test_interrupted_collection_rolls_back_vectors_status_and_claims(client, window_book):
    factory, settings, book_id = window_book
    rebuild_book_windows(factory, settings, book_id, len)
    enqueue_book_embeddings(factory, book_id)
    batch_id, payload, _, _, _ = create_embedding_batch_request_input(factory, settings, len)
    with factory.begin() as db:
        db.get(EmbeddingBatchJob, batch_id).state = "submitted"
    results = "\n".join(
        json.dumps({"key": row["key"], "response": {"embedding": {"values": [0.5] * 768}}})
        for row in map(json.loads, payload.splitlines())
    ).encode()

    def interrupt_commit(db):
        raise RuntimeError("Interrupted collection")

    event.listen(factory, "before_commit", interrupt_commit)
    try:
        with pytest.raises(RuntimeError, match="Interrupted collection"):
            publish_embedding_results(factory, settings, batch_id, results)
    finally:
        event.remove(factory, "before_commit", interrupt_commit)

    with factory() as db:
        assert all(row.vector is None for row in db.scalars(select(WindowEmbedding)))
        assert db.get(EmbeddingBatchJob, batch_id).state == "submitted"
        assert all(row.active for row in db.scalars(select(EmbeddingBatchRequest)))
        assert db.get(BookBatchJob, (book_id, "embeddings")).active_batch_id == batch_id
