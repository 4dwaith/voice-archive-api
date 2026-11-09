import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest

from voice_archive_api.core.config import Settings
from voice_archive_api.logic.library import EvidenceSegment
from voice_archive_api.logic.search.retrieval import SegmentCandidate
from voice_archive_api.services.library.search import reranking
from voice_archive_api.services.library.search.expansion import ExpandedSegmentCandidate


def create_expanded_candidate(identity):
    evidence = EvidenceSegment(
        segment_id=identity,
        book_id=1,
        content_version=uuid4(),
        section_id=None,
        path=["Book"],
        sequence=identity,
        text=f"Target {identity} ಕನ್ನಡ",
    )
    candidate = SegmentCandidate(identity, 1, (uuid4(),), 0.1, (0, identity, 0))

    neighbor = evidence.model_copy(update={"segment_id": identity + 100, "sequence": identity - 1, "text": "Before"})

    return ExpandedSegmentCandidate(candidate, evidence, "Context summary", (neighbor,), ())


@pytest.fixture(name="rerank_settings")
def create_rerank_settings():
    return Settings(
        postgres_user="test", postgres_password="secret", postgres_db="test", reranker_base_url="http://reranker:8000"
    )


def install_reranker_transport(monkeypatch, respond):
    original = httpx.AsyncClient

    def create_client(**kwargs):
        return original(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(reranking.httpx, "AsyncClient", create_client)


def test_batches_map_reordered_indices_back_to_original_candidates(monkeypatch, rerank_settings):
    expanded = [create_expanded_candidate(identity) for identity in range(5)]
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        batch_score = len(requests) * 10

        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": index, "relevance_score": float(batch_score + index)}
                    for index in reversed(range(len(payload["documents"])))
                ]
            },
        )

    install_reranker_transport(monkeypatch, respond)
    monkeypatch.setattr(reranking, "BATCH_SIZE", 2)

    ranked = asyncio.run(reranking.rerank_segment_candidates(rerank_settings, "Question?", expanded))

    assert [candidate.segment_id for candidate in ranked] == list(range(5))
    assert [candidate.score for candidate in ranked] == [10, 11, 20, 21, 30]
    assert all(
        candidate.window_ids == item.candidate.window_ids for candidate, item in zip(ranked, expanded, strict=True)
    )
    assert [request["top_n"] for request in requests] == [2, 2, 1]
    assert json.loads(requests[0]["documents"][0])["TARGET"] == expanded[0].evidence.text


@pytest.mark.parametrize(
    "results",
    [
        [],
        [{"index": 0, "relevance_score": 1.0}] * 2,
        [{"index": 1, "relevance_score": 1.0}],
        [{"index": True, "relevance_score": 1.0}],
        [{"index": 0, "relevance_score": float("nan")}],
        [{"index": 0, "relevance_score": "1.0"}],
    ],
)
def test_invalid_results_never_produce_partial_rankings(monkeypatch, rerank_settings, results):
    install_reranker_transport(
        monkeypatch, lambda request: httpx.Response(200, content=json.dumps({"results": results}))
    )

    with pytest.raises(ValueError):
        asyncio.run(reranking.rerank_segment_candidates(rerank_settings, "Question?", [create_expanded_candidate(1)]))


def test_input_bounds_remove_whole_context_without_cutting_the_target():
    item = replace(create_expanded_candidate(1), summary="s" * 5000)
    item.preceding_segments[0].text = "Long context " * 500

    document = json.loads(reranking.format_reranking_document(item, "Question?", 1024))

    assert document["TARGET"] == item.evidence.text
    assert document["BEFORE"] == []
    assert document["SUMMARY"] is None

    with pytest.raises(ValueError):
        reranking.format_reranking_document(item, "Huge question " * 500, 1024)


def test_cancellation_propagates(monkeypatch, rerank_settings):
    async def check_cancellation():
        started = asyncio.Event()

        async def respond(request):
            started.set()
            await asyncio.Event().wait()

        install_reranker_transport(monkeypatch, respond)
        task = asyncio.create_task(
            reranking.rerank_segment_candidates(rerank_settings, "Question?", [create_expanded_candidate(1)])
        )
        await started.wait()

        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(check_cancellation())
