"""Read candidate passages, summaries and local context in one source-consistent stage."""

from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from voice_archive_api.core.config import Settings
from voice_archive_api.db.models.content import BookSegment
from voice_archive_api.logic.library import EvidenceSegment
from voice_archive_api.logic.search.retrieval import SegmentCandidate
from voice_archive_api.services.library.schemas import AskRequest
from voice_archive_api.services.library.search.retrieval import (
    MAX_EVIDENCE_BYTES,
    EvidenceTooLargeError,
    LibrarySourceSnapshot,
    SourceChangedError,
    read_evidence_in_session,
    require_current_source,
)


@dataclass(frozen=True)
class SegmentExpansionOptions:
    preceding_segments: int = 2
    following_segments: int = 1
    max_input_bytes: int = MAX_EVIDENCE_BYTES

    def __post_init__(self) -> None:
        if self.preceding_segments < 0 or self.following_segments < 0 or self.max_input_bytes <= 0:
            raise ValueError("Context counts must be nonnegative and the input byte budget must be positive")


@dataclass(frozen=True)
class ExpandedSegmentCandidate:
    candidate: SegmentCandidate
    evidence: EvidenceSegment
    summary: str | None
    preceding_segments: tuple[EvidenceSegment, ...]
    following_segments: tuple[EvidenceSegment, ...]


def expand_segment_candidates(
    factory: sessionmaker[Session],
    settings: Settings,
    request: AskRequest,
    snapshot: LibrarySourceSnapshot,
    candidates: list[SegmentCandidate],
    options: SegmentExpansionOptions | None = None,
) -> list[ExpandedSegmentCandidate]:
    """Keep targets distinct from neighbors; bound bytes before loading text.

    Neighbors are the nearest rows in the same book and section, even when
    sequence numbers have gaps. Counts are provisional; provider token limits
    belong to the eventual reranker. The byte guard includes repeated context.
    """
    options = options or SegmentExpansionOptions()
    if request.use_whole_book:
        raise ValueError("Candidate expansion requires passage-search mode")
    with factory.begin() as db:
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        require_current_source(db, request, snapshot)
        if not candidates:
            return []
        book_ids = [book.id for book in snapshot.books]
        expected_books = {candidate.segment_id: candidate.book_id for candidate in candidates}
        positions = (
            select(
                BookSegment.id,
                BookSegment.book_id,
                BookSegment.section_id,
                BookSegment.sequence,
                func.row_number()
                .over(partition_by=(BookSegment.book_id, BookSegment.section_id), order_by=BookSegment.sequence)
                .label("position"),
            )
            .where(BookSegment.book_id.in_(book_ids))
            .cte("segment_positions")
        )
        targets = positions.alias("targets")
        neighbors = positions.alias("neighbors")
        pairs = (
            select(targets.c.id.label("target_id"), neighbors.c.id.label("neighbor_id"), neighbors.c.sequence)
            .select_from(targets)
            .join(
                neighbors,
                (neighbors.c.book_id == targets.c.book_id)
                & neighbors.c.section_id.is_not_distinct_from(targets.c.section_id)
                & neighbors.c.position.between(
                    targets.c.position - options.preceding_segments,
                    targets.c.position + options.following_segments,
                ),
            )
            .where(targets.c.id.in_(expected_books))
            .subquery()
        )
        found_books = {
            identity: book_id
            for identity, book_id in db.execute(
                select(positions.c.id, positions.c.book_id).where(positions.c.id.in_(expected_books))
            )
        }
        if found_books != expected_books:
            raise SourceChangedError
        size = db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        func.octet_length(BookSegment.text_content)
                        + case(
                            (
                                pairs.c.target_id == pairs.c.neighbor_id,
                                func.coalesce(func.octet_length(BookSegment.summary), 0),
                            ),
                            else_=0,
                        )
                        + 256
                    ),
                    0,
                )
            )
            .select_from(pairs)
            .join(BookSegment, BookSegment.id == pairs.c.neighbor_id)
        )
        if size is not None and size > min(options.max_input_bytes, MAX_EVIDENCE_BYTES):
            raise EvidenceTooLargeError
        context_ids: dict[int, list[int]] = {}
        for row in db.execute(select(pairs).order_by(pairs.c.target_id, pairs.c.sequence)):
            context_ids.setdefault(row.target_id, []).append(row.neighbor_id)
        segment_ids = list(dict.fromkeys(identity for identities in context_ids.values() for identity in identities))
        evidence = {
            segment.segment_id: segment
            for segment in read_evidence_in_session(db, settings, request, snapshot, segment_ids)
        }
        summaries = {
            identity: summary
            for identity, summary in db.execute(
                select(BookSegment.id, BookSegment.summary).where(BookSegment.id.in_(expected_books))
            )
        }
        expanded = []
        for candidate in candidates:
            target = evidence[candidate.segment_id]
            context = [evidence[identity] for identity in context_ids[candidate.segment_id]]
            expanded.append(
                ExpandedSegmentCandidate(
                    candidate=candidate,
                    evidence=target,
                    summary=summaries[candidate.segment_id],
                    preceding_segments=tuple(segment for segment in context if segment.sequence < target.sequence),
                    following_segments=tuple(segment for segment in context if segment.sequence > target.sequence),
                )
            )
        return expanded
