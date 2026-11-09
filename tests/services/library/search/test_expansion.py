from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSegment
from voice_archive_api.logic.search.retrieval import SegmentCandidate
from voice_archive_api.services.library.schemas import AskRequest
from voice_archive_api.services.library.search import expansion
from voice_archive_api.services.library.search.expansion import SegmentExpansionOptions, expand_segment_candidates
from voice_archive_api.services.library.search.retrieval import (
    EvidenceTooLargeError,
    SourceChangedError,
    read_source_snapshot,
)


@pytest.fixture(name="expansion_case")
def create_expansion_case(client):
    book_id = client.post("/api/v1/books", json={"title": "Context", "language": "en"}).json()["id"]
    response = client.post(
        f"/api/v1/books/{book_id}/content-imports",
        json={
            "schema_version": 1,
            "sections": [
                {"source_key": key, "kind": "chapter", "title": key, "sibling_sequence": index}
                for index, key in enumerate(["First", "Second"])
            ],
            "segments": [
                {
                    "source_key": str(index),
                    "sequence": index,
                    "kind": "prose",
                    "section_source_key": "First" if index < 5 else "Second",
                    "html_content": f"<p>Passage {index}.</p>",
                }
                for index in range(7)
            ],
        },
    )
    assert response.status_code == 200, response.text

    factory = client.app.state.session_factory
    with factory.begin() as db:
        rows = db.scalars(
            select(BookSegment).where(BookSegment.book_id == book_id).order_by(BookSegment.sequence)
        ).all()
        # Gaps make neighbor selection independent of consecutive sequence values.
        for row in reversed(rows):
            row.sequence *= 10
            row.summary = f"Summary for {row.text_content}"
        identities = [row.id for row in rows]

    request = AskRequest(book_id=book_id, query="What happened?", query_en="What happened?")
    snapshot = read_source_snapshot(factory, request)
    candidates = [
        SegmentCandidate(identity, book_id, (uuid4(),), 0.1, (0, 0, index)) for index, identity in enumerate(identities)
    ]

    return factory, client.app.state.settings, request, snapshot, candidates


def test_expansion_reads_targets_and_ordered_neighbors_with_shared_evidence(expansion_case):
    factory, settings, request, snapshot, candidates = expansion_case

    expanded = expand_segment_candidates(factory, settings, request, snapshot, [candidates[3], candidates[2]])

    first, second = expanded

    assert [item.candidate for item in expanded] == [candidates[3], candidates[2]]
    assert first.evidence.text == "Passage 3."
    assert first.summary == "Summary for Passage 3."
    assert first.evidence.path == ["Context", "First"]
    assert [segment.text for segment in first.preceding_segments] == ["Passage 1.", "Passage 2."]
    assert [segment.text for segment in first.following_segments] == ["Passage 4."]
    assert first.preceding_segments[1] is second.evidence
    assert first.evidence is second.following_segments[0]


@pytest.mark.parametrize("index, before, after", [(0, [], [1]), (4, [2, 3], []), (5, [], [6]), (6, [5], [])])
def test_expansion_stops_at_section_edges(expansion_case, index, before, after):
    factory, settings, request, snapshot, candidates = expansion_case

    expanded = expand_segment_candidates(factory, settings, request, snapshot, [candidates[index]])[0]

    assert [segment.segment_id for segment in expanded.preceding_segments] == [candidates[i].segment_id for i in before]
    assert [segment.segment_id for segment in expanded.following_segments] == [candidates[i].segment_id for i in after]


def create_sectionless_book(client, title):
    book_id = client.post("/api/v1/books", json={"title": title, "language": "en"}).json()["id"]
    response = client.post(
        f"/api/v1/books/{book_id}/content-imports",
        json={
            "schema_version": 1,
            "sections": [],
            "segments": [
                {
                    "source_key": str(index),
                    "sequence": index,
                    "kind": "prose",
                    "html_content": f"<p>{title} passage {index}.</p>",
                }
                for index in range(3)
            ],
        },
    )
    assert response.status_code == 200, response.text

    return book_id


def test_expansion_keeps_neighbors_in_the_target_book_within_library_scope(client):
    first_book_id = create_sectionless_book(client, "First book")
    second_book_id = create_sectionless_book(client, "Second book")
    factory = client.app.state.session_factory
    request = AskRequest(query="What happened?", query_en="What happened?")
    snapshot = read_source_snapshot(factory, request)
    with factory() as db:
        targets = db.scalars(
            select(BookSegment)
            .where(BookSegment.book_id.in_([first_book_id, second_book_id]), BookSegment.sequence == 1)
            .order_by(BookSegment.book_id)
        ).all()
        candidates = [
            SegmentCandidate(row.id, row.book_id, (uuid4(),), 0.1, (index, 0, 0)) for index, row in enumerate(targets)
        ]

    expanded = expand_segment_candidates(factory, client.app.state.settings, request, snapshot, candidates)

    assert {book.id for book in snapshot.books} == {first_book_id, second_book_id}
    assert len(expanded) == 2
    for item, title in zip(expanded, ["First book", "Second book"], strict=True):
        assert item.evidence.text == f"{title} passage 1."
        assert [segment.text for segment in item.preceding_segments] == [f"{title} passage 0."]
        assert [segment.text for segment in item.following_segments] == [f"{title} passage 2."]
        assert all(
            segment.book_id == item.candidate.book_id
            for segment in (*item.preceding_segments, item.evidence, *item.following_segments)
        )


def test_expansion_allows_missing_summaries(expansion_case):
    factory, settings, request, snapshot, candidates = expansion_case
    with factory.begin() as db:
        db.execute(update(BookSegment).where(BookSegment.id == candidates[2].segment_id).values(summary=None))

    expanded = expand_segment_candidates(factory, settings, request, snapshot, [candidates[2]])[0]

    assert expanded.summary is None


def test_expansion_can_disable_neighbor_context(expansion_case):
    factory, settings, request, snapshot, candidates = expansion_case

    expanded = expand_segment_candidates(
        factory, settings, request, snapshot, [candidates[2]], SegmentExpansionOptions(0, 0)
    )[0]

    assert expanded.preceding_segments == ()
    assert expanded.following_segments == ()


@pytest.mark.parametrize("problem", ["missing", "wrong_book", "stale"])
def test_expansion_rejects_missing_wrong_book_and_stale_candidates(expansion_case, problem):
    factory, settings, request, snapshot, candidates = expansion_case
    candidate = candidates[0]
    if problem == "missing":
        candidate = replace(candidate, segment_id=9_000_000)
    elif problem == "wrong_book":
        candidate = replace(candidate, book_id=candidate.book_id + 1)
    else:
        with factory.begin() as db:
            db.execute(update(Book).where(Book.id == candidate.book_id).values(content_version=uuid4()))

    with pytest.raises(SourceChangedError):
        expand_segment_candidates(factory, settings, request, snapshot, [candidate])


def test_expansion_rejects_oversize_before_fetching_text(expansion_case, monkeypatch):
    factory, settings, request, snapshot, candidates = expansion_case
    payload_reads = []

    def reject_payload_read(*args, **kwargs):
        payload_reads.append(True)
        pytest.fail("Oversize expansion must stop before loading passage text")

    monkeypatch.setitem(expansion.__dict__, "read_evidence_in_session", reject_payload_read)

    with pytest.raises(EvidenceTooLargeError):
        expand_segment_candidates(
            factory, settings, request, snapshot, candidates, SegmentExpansionOptions(max_input_bytes=1)
        )

    assert payload_reads == []


def test_expansion_returns_no_targets_for_an_empty_pool(expansion_case):
    factory, settings, request, snapshot, _ = expansion_case

    expanded = expand_segment_candidates(factory, settings, request, snapshot, [])

    assert expanded == []
