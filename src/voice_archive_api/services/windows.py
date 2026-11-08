"""Rebuild windows, assembled text and empty embedding rows in one transaction."""

from collections.abc import Callable
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session, sessionmaker

from voice_archive_api.core.config import Settings
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.windows import RetrievalWindow, WindowEmbedding
from voice_archive_api.logic.books.validation import resolve_primary_language
from voice_archive_api.logic.windows import (
    WindowSegment,
    build_windows,
    compute_embedding_input_hash,
)
from voice_archive_api.services.books.service import BookNotFound, lock_book_for_series_write


def rebuild_book_windows(
    factory: sessionmaker[Session],
    settings: Settings,
    book_id: int,
    count_tokens: Callable[[str], int],
    *,
    dry_run: bool = False,
    window_tokens: int | None = None,
    overlap_tokens: int = 125,
) -> list[dict[str, Any]]:
    with factory.begin() as db:
        book = lock_book_for_series_write(db, book_id, {})
        if book is None:
            raise BookNotFound
        if book.content_version is None:
            raise ValueError("Import canonical text before building windows")
        language = resolve_primary_language(book.language)
        sections = {row.id: row for row in db.scalars(select(BookSection).where(BookSection.book_id == book_id))}
        book_path = [*([book.series_title] if book.series_title else []), book.title]
        segments = []
        for row in db.scalars(select(BookSegment).where(BookSegment.book_id == book_id).order_by(BookSegment.sequence)):
            titles = []
            section_id = row.section_id
            while section_id is not None:
                section = sections[section_id]
                titles.append(section.title)
                section_id = section.parent_id
            segments.append(
                WindowSegment(
                    row.id,
                    row.section_id,
                    " > ".join([*book_path, *reversed(titles)]),
                    row.text_content,
                    row.english_translation,
                )
            )
        if not segments:
            raise ValueError("Book has no canonical text segments")
        windows = build_windows(
            segments, language, count_tokens, window_tokens=window_tokens, overlap_tokens=overlap_tokens
        )
        if not dry_run:
            window_rows = []
            embedding_rows = []
            for sequence, content in enumerate(windows):
                window_id = uuid4()
                window_rows.append(
                    dict(
                        id=window_id,
                        book_id=book_id,
                        content_version=book.content_version,
                        sequence=sequence,
                        **asdict(content),
                    )
                )
                for kind, text in (("primary", content.primary_text), ("translated", content.translated_text)):
                    if text is not None:
                        embedding_rows.append(
                            dict(
                                id=uuid4(),
                                window_id=window_id,
                                kind=kind,
                                model=settings.gemini_embedding_model,
                                dimensions=settings.gemini_embedding_dimensions,
                                input_format=settings.embedding_input_format,
                                input_hash=compute_embedding_input_hash(
                                    content.path,
                                    text,
                                    model=settings.gemini_embedding_model,
                                    dimensions=settings.gemini_embedding_dimensions,
                                    input_format=settings.embedding_input_format,
                                ),
                                vector=None,
                            )
                        )
            db.execute(delete(RetrievalWindow).where(RetrievalWindow.book_id == book_id))
            db.execute(insert(RetrievalWindow), window_rows)
            db.execute(insert(WindowEmbedding), embedding_rows)
        return [asdict(content) for content in windows]


def clear_book_windows(factory: sessionmaker[Session], book_id: int) -> None:
    with factory.begin() as db:
        if lock_book_for_series_write(db, book_id, {}) is None:
            raise BookNotFound
        db.execute(delete(RetrievalWindow).where(RetrievalWindow.book_id == book_id))
