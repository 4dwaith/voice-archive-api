"""Transaction rollback and foreign-key cascading for retrieval windows."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete, event, select

from voice_archive_api.db.models.content import BookSegment
from voice_archive_api.db.models.embedding_batch import EmbeddingBatchJob, EmbeddingBatchRequest
from voice_archive_api.db.models.windows import RetrievalWindow, WindowEmbedding
from voice_archive_api.services.embedding_batches import claim_embedding_batch
from voice_archive_api.services.embedding_collection import publish_embedding_results
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


def test_concurrent_batch_claims_do_not_duplicate_inputs(window_book):
    factory, settings, book_id = window_book
    rebuild_book_windows(factory, settings, book_id, len)

    with ThreadPoolExecutor(max_workers=2) as workers:
        claims = list(
            workers.map(
                lambda _: claim_embedding_batch(factory, book_id, settings.gemini_embedding_model),
                range(2),
            )
        )

    assert sum(claim is not None for claim in claims) == 1


def test_interrupted_collection_rolls_back_vectors_status_and_claims(client, window_book):
    factory, settings, book_id = window_book
    rebuild_book_windows(factory, settings, book_id, len)
    batch_id, payload, _ = claim_embedding_batch(factory, book_id, settings.gemini_embedding_model)
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
