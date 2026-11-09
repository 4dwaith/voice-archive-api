"""Rank original window text inside the dense search's eligible snapshot."""

import unicodedata

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.windows import RetrievalWindow
from voice_archive_api.logic.dense import CANDIDATE_LIMIT, WindowMatch


def search_lexical_windows(
    db: Session, book_ids: list[int], is_english: bool, query: str, lexical_terms: list[str] | None = None
) -> list[WindowMatch]:
    terms = list(dict.fromkeys(unicodedata.normalize("NFC", term.strip()) for term in (lexical_terms or [query])))
    if is_english:
        queries = [func.websearch_to_tsquery("english", term) for term in terms]
        predicates = [RetrievalWindow.english_vector.bool_op("@@")(query) for query in queries]
        scores = [func.ts_rank_cd(RetrievalWindow.english_vector, query) for query in queries]
        language_filter = RetrievalWindow.translated_text.is_(None)
    else:
        # Transaction-local threshold also controls the index-supported operator.
        db.execute(select(func.set_config("pg_trgm.word_similarity_threshold", "0.45", True)))
        predicates = [RetrievalWindow.trigram_text.bool_op("%>")(term) for term in terms]
        scores = [func.word_similarity(term, RetrievalWindow.trigram_text) for term in terms]
        language_filter = RetrievalWindow.translated_text.is_not(None)
    score = func.greatest(*scores) if len(scores) > 1 else scores[0]
    rows = db.execute(
        select(
            RetrievalWindow.id,
            RetrievalWindow.book_id,
            RetrievalWindow.sequence,
            RetrievalWindow.segment_ids,
            (-score).label("lexical_distance"),
        )
        .join(Book, Book.id == RetrievalWindow.book_id)
        .where(
            RetrievalWindow.book_id.in_(book_ids),
            RetrievalWindow.content_version == Book.content_version,
            language_filter,
            or_(*predicates),
        )
        .order_by(-score, Book.series_id, Book.series_index, Book.id, RetrievalWindow.sequence)
        .limit(CANDIDATE_LIMIT)
    )
    return [WindowMatch(row.id, row.book_id, row.sequence, row.segment_ids, row.lexical_distance) for row in rows]
