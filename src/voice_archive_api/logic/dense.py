"""Fuse bounded database search results; never receive or compare stored vectors."""

import math
from dataclasses import dataclass
from uuid import UUID

CANDIDATE_LIMIT = 75
SEGMENT_TARGET = 25
RRF_CONSTANT = 60


@dataclass(frozen=True)
class WindowMatch:
    id: UUID
    book_id: int
    sequence: int
    segment_ids: list[int]
    distance: float


def resolve_query_texts(language: str, query: str, query_en: str) -> dict[str, str]:
    return {"primary": query_en} if language == "en" else {"primary": query, "translated": query_en}


def validate_query_vector(vector: list[float], dimensions: int) -> None:
    if (
        len(vector) != dimensions
        or not all(math.isfinite(value) for value in vector)
        or not 0 < math.hypot(*vector) < math.inf
    ):
        raise ValueError("Invalid query embedding dimensions, values or magnitude")


def select_segment_ids(ranked_lists: dict[str, list[WindowMatch]], book_ids: list[int]) -> list[int]:
    book_order = {book_id: index for index, book_id in enumerate(book_ids)}
    scores: dict[UUID, float] = {}
    windows: dict[UUID, WindowMatch] = {}

    for matches in ranked_lists.values():
        ordered = sorted(matches, key=lambda w: (w.distance, book_order[w.book_id], w.sequence))
        unique: dict[UUID, WindowMatch] = {}

        for window in ordered:
            unique.setdefault(window.id, window)

        for rank, window in enumerate(unique.values(), start=1):
            windows[window.id] = window
            scores[window.id] = scores.get(window.id, 0) + 1 / (RRF_CONSTANT + rank)

    segment_scores: dict[int, float] = {}
    source_order: dict[int, tuple[int, int, int]] = {}

    for window in windows.values():
        for position, segment_id in enumerate(window.segment_ids):
            segment_scores[segment_id] = max(segment_scores.get(segment_id, 0), scores[window.id])
            order = (book_order[window.book_id], window.sequence, position)
            source_order[segment_id] = min(source_order.get(segment_id, order), order)

    return sorted(segment_scores, key=lambda identity: (-segment_scores[identity], source_order[identity]))[
        :SEGMENT_TARGET
    ]
