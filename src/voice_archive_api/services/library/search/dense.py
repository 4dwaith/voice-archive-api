"""Exact pgvector search within eligible books."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from voice_archive_api.core.config import Settings
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.windows import RetrievalWindow, WindowEmbedding
from voice_archive_api.logic.search.retrieval import CANDIDATE_LIMIT, WindowMatch


def search_dense_windows(
    db: Session,
    settings: Settings,
    book_ids: list[int],
    kind: str,
    vector: list[float],
) -> list[WindowMatch]:
    distance = WindowEmbedding.vector.cosine_distance(vector)
    rows = db.execute(
        select(
            RetrievalWindow.id,
            RetrievalWindow.book_id,
            RetrievalWindow.sequence,
            RetrievalWindow.segment_ids,
            distance.label("distance"),
        )
        .join(WindowEmbedding)
        .join(Book, Book.id == RetrievalWindow.book_id)
        .where(
            RetrievalWindow.book_id.in_(book_ids),
            RetrievalWindow.content_version == Book.content_version,
            WindowEmbedding.kind == kind,
            WindowEmbedding.model == settings.gemini_embedding_model,
            WindowEmbedding.dimensions == settings.gemini_embedding_dimensions,
            WindowEmbedding.input_format == settings.embedding_input_format,
            WindowEmbedding.vector.is_not(None),
            func.vector_norm(WindowEmbedding.vector) > 0,
        )
        .order_by(distance, Book.series_id, Book.series_index, Book.id, RetrievalWindow.sequence)
        .limit(CANDIDATE_LIMIT)
    )
    return [WindowMatch(**row._mapping) for row in rows]
