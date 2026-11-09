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


@dataclass(frozen=True)
class RankedWindow:
    window: WindowMatch
    score: float


@dataclass(frozen=True)
class SegmentCandidate:
    segment_id: int
    book_id: int
    window_ids: tuple[UUID, ...]
    score: float
    source_order: tuple[int, int, int]


def resolve_query_texts(language: str, query: str, query_en: str) -> dict[str, str]:
    return {"primary": query_en} if language == "en" else {"primary": query, "translated": query_en}


def validate_query_vector(vector: list[float], dimensions: int) -> None:
    if (
        len(vector) != dimensions
        or not all(math.isfinite(value) for value in vector)
        or not 0 < math.hypot(*vector) < math.inf
    ):
        raise ValueError("Invalid query embedding dimensions, values or magnitude")


def fuse_window_rankings(ranked_lists: dict[str, list[WindowMatch]], book_ids: list[int]) -> list[RankedWindow]:
    """Combine channel votes without expanding or truncating the candidate pool."""
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

    return sorted(
        [RankedWindow(window, scores[window.id]) for window in windows.values()],
        key=lambda ranked: (-ranked.score, book_order[ranked.window.book_id], ranked.window.sequence),
    )


def expand_segment_candidates(ranked_windows: list[RankedWindow], book_ids: list[int]) -> list[SegmentCandidate]:
    """Deduplicate segments, retaining provenance and the strongest window score."""
    book_order = {book_id: index for index, book_id in enumerate(book_ids)}
    candidates: dict[int, SegmentCandidate] = {}

    for ranked in ranked_windows:
        window = ranked.window
        for position, segment_id in enumerate(window.segment_ids):
            previous = candidates.get(segment_id)
            order = (book_order[window.book_id], window.sequence, position)
            candidates[segment_id] = SegmentCandidate(
                segment_id=segment_id,
                book_id=window.book_id,
                window_ids=(*previous.window_ids, window.id) if previous else (window.id,),
                score=max(previous.score, ranked.score) if previous else ranked.score,
                source_order=min(previous.source_order, order) if previous else order,
            )

    return list(candidates.values())


def select_segment_ids(candidates: list[SegmentCandidate], *, limit: int = SEGMENT_TARGET) -> list[int]:
    """Apply the final evidence cap after candidate ranking stages have completed."""
    if limit < 0:
        raise ValueError("Segment limit must be nonnegative")
    return [
        candidate.segment_id
        for candidate in sorted(candidates, key=lambda candidate: (-candidate.score, candidate.source_order))[:limit]
    ]
