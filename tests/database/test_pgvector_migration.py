from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command


def test_embedding_conversion_preserves_values_pending_rows_and_request_identities(test_engine, migration_config):
    command.downgrade(migration_config, "0025_batch_state_constraints")
    window_id, embedding_id, pending_id, batch_id, request_id = (uuid4() for _ in range(5))
    values = [0.123456789, -0.333333333, 0.987654321]
    with test_engine.begin() as db:
        book_id = db.scalar(
            text("""INSERT INTO books
            (title, normalized_title, normalized_author, language, created_at, updated_at)
            VALUES ('Stored embeddings', 'stored embeddings', '', 'en', now(), now()) RETURNING id""")
        )
        db.execute(
            text("""INSERT INTO retrieval_windows
            (id, book_id, content_version, sequence, segment_ids, path, primary_text)
            VALUES (:window, :book, :version, 0, '{}', 'Stored embeddings', 'Stored source')"""),
            {"window": window_id, "book": book_id, "version": uuid4()},
        )
        for identity, kind, vector in [(embedding_id, "primary", values), (pending_id, "translated", None)]:
            db.execute(
                text("""INSERT INTO window_embeddings
                (id, window_id, kind, model, dimensions, input_format, input_hash, vector)
                VALUES (:id, :window, :kind, 'gemini-embedding-2', 3, 'retrieval-document-v1', :hash, :vector)"""),
                {"id": identity, "window": window_id, "kind": kind, "hash": "a" * 64, "vector": vector},
            )
        db.execute(
            text("""INSERT INTO embedding_batch_jobs (id, book_id, model, state)
            VALUES (:batch, :book, 'gemini-embedding-2', 'submitted')"""),
            {"batch": batch_id, "book": book_id},
        )
        db.execute(
            text("""INSERT INTO embedding_batch_requests (id, batch_id, embedding_id, input_hash, active)
            VALUES (:request, :batch, :embedding, :hash, true)"""),
            {"request": request_id, "batch": batch_id, "embedding": embedding_id, "hash": "a" * 64},
        )

    command.upgrade(migration_config, "head")

    with test_engine.connect() as db:
        stored, kind, dimensions, input_hash = db.execute(
            text("""SELECT vector::real[]::double precision[],
            pg_typeof(vector)::text, dimensions, input_hash FROM window_embeddings WHERE id = :id"""),
            {"id": embedding_id},
        ).one()
        assert stored == pytest.approx(values, abs=1e-7)
        assert (kind, dimensions, input_hash) == ("vector", 3, "a" * 64)
        assert db.scalar(text("SELECT vector IS NULL FROM window_embeddings WHERE id = :id"), {"id": pending_id})
        assert db.execute(
            text("SELECT embedding_id, input_hash, active FROM embedding_batch_requests WHERE id = :id"),
            {"id": request_id},
        ).one() == (embedding_id, "a" * 64, True)

    with pytest.raises(IntegrityError), test_engine.begin() as db:
        db.execute(text("UPDATE window_embeddings SET vector = '[1,2]'::vector WHERE id = :id"), {"id": embedding_id})

    command.downgrade(migration_config, "0025_batch_state_constraints")

    with test_engine.connect() as db:
        stored, kind = db.execute(
            text("SELECT vector, pg_typeof(vector)::text FROM window_embeddings WHERE id = :id"), {"id": embedding_id}
        ).one()
        assert stored == pytest.approx(values, abs=1e-7)
        assert kind == "double precision[]"
        assert db.scalar(text("SELECT vector IS NULL FROM window_embeddings WHERE id = :id"), {"id": pending_id})

    command.upgrade(migration_config, "head")
