"""Batch state constraints apply even when callers bypass ORM validation."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO book_batch_jobs (book_id, kind, state) VALUES (1, 'embeddings', :state)",
        "INSERT INTO embedding_batch_jobs (id, book_id, model, state) VALUES (:id, 1, 'model', :state)",
    ],
)
def test_database_rejects_unknown_batch_states(test_engine, statement):
    with pytest.raises(IntegrityError), test_engine.begin() as connection:
        connection.execute(text(statement), {"id": uuid4(), "state": "unknown"})
