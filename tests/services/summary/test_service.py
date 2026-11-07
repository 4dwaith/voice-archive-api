"""Provider batching, cache retry and generation lifecycle orchestration."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from google.genai import Client
from pydantic import SecretStr
from sqlalchemy import select

from tests.summary_helpers import create_summary_drafts
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSegment
from voice_archive_api.db.models.summary import SummaryGenerationRun
from voice_archive_api.logic.summaries import POLICY, SUMMARY_INSTRUCTIONS, GeneratedSummary
from voice_archive_api.services.books.schemas import BookPatch
from voice_archive_api.services.books.service import patch_book
from voice_archive_api.services.library.provider import BookTooLargeError
from voice_archive_api.services.summary import service as summaries


@pytest.mark.parametrize("cached_request_fails", [False, True])
def test_batched_roots_keep_full_context_and_carry_earlier_summaries(
    client, summary_series, monkeypatch, cached_request_fails
):
    _, _, current_id = summary_series
    requests = []
    cache_events = []
    cached_source = None
    generation_attempts = 0
    client.app.state.settings.gemini_batch_api_key = SecretStr("provider-batch-secret")
    client.app.state.settings.gemini_api_key = SecretStr("provider-interactive-secret")

    async def respond(request):
        nonlocal cached_source, generation_attempts
        payload = json.loads(request.content) if request.content else {}
        if request.url.path.endswith(":countTokens"):
            if "generateContentRequest" in payload:
                counted = payload["generateContentRequest"]
                if counted.get("cachedContent"):
                    assert counted["cachedContent"] == "cachedContents/root"
                    assert "systemInstruction" not in counted
                    assert len(counted["contents"][0]["parts"]) == 1
            return httpx.Response(200, json={"totalTokens": 8000})
        if "/cachedContents" in request.url.path:
            cache_events.append(request.method)
            if request.method == "DELETE":
                return httpx.Response(200, json={})
            assert payload["ttl"] == "1200s"
            assert payload["systemInstruction"]["parts"][0]["text"] == SUMMARY_INSTRUCTIONS
            cached_source = payload["contents"][0]["parts"][0]["text"]
            return httpx.Response(
                200,
                json={
                    "name": "cachedContents/root",
                    "expireTime": (datetime.now(UTC) + timedelta(minutes=20)).isoformat(),
                },
            )
        if not request.url.path.endswith(":generateContent"):
            return httpx.Response(200, json={"inputTokenLimit": 1_048_576, "outputTokenLimit": 65_536})
        generation_attempts += 1
        if cached_request_fails and generation_attempts == 1:
            return httpx.Response(404, json={"error": {"code": 404, "message": "Cache unavailable"}})
        parts = [part["text"] for part in payload["contents"][0]["parts"]]
        if payload.get("cachedContent"):
            assert "systemInstruction" not in payload
            assert len(parts) == 1
            parts.insert(0, cached_source)
        requests.append(parts)
        result = {
            "summaries": [
                GeneratedSummary(
                    kind=target["kind"], id=target["id"], summary="A traveller describes a journey."
                ).model_dump()
                for target in json.loads(parts[1])["TARGETS"]
            ]
        }
        return httpx.Response(
            200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(result)}]}}]}
        )

    def create_test_client(*, api_key, http_options):
        assert api_key == "provider-batch-secret"
        http_options.async_client_args = {"transport": httpx.MockTransport(respond)}
        return Client(api_key=api_key, http_options=http_options)

    monkeypatch.setattr("voice_archive_api.services.library.provider.genai.Client", create_test_client)
    monkeypatch.setattr("voice_archive_api.services.summary.cache.genai.Client", create_test_client)

    asyncio.run(
        summaries.generate_book_summaries(client.app.state.session_factory, client.app.state.settings, current_id)
    )

    assert len(requests) == 4
    assert generation_attempts == (5 if cached_request_fails else 4)
    assert cache_events == ["POST", "DELETE"]
    assert requests[0][0] == requests[1][0]
    first_targets = json.loads(requests[0][1])["TARGETS"]
    assert first_targets[0]["end_fragment_index"] == 1
    assert first_targets[1]["end_fragment_index"] == 2
    root_targets = first_targets + json.loads(requests[1][1])["TARGETS"]
    assert [target["end_fragment_index"] for target in root_targets] == sorted(
        target["end_fragment_index"] for target in root_targets
    )
    assert root_targets[-1]["kind"] == "section"
    first, second = [json.loads(requests[index][0]) for index in (0, 2)]
    assert len(first["SOURCE"]["fragments"]) == 41
    assert len(second["SOURCE"]["fragments"]) == 2
    assert second["BACKGROUND"]["previous_root_text"] == "\n\n".join(
        [
            "departure",
            *(f["text"] for f in first["SOURCE"]["fragments"] if f["kind"] == "passage"),
        ]
    )
    assert second["BACKGROUND"]["previous_roots"] == [
        {"title": "departure", "summary": "A traveller describes a journey."}
    ]
    assert first["BACKGROUND"]["previous_books"] == [
        {"title": "Journey", "summary": "The book describes a return home."}
    ]
    with client.app.state.session_factory() as db:
        assert all(
            row.english_translation is None
            for row in db.scalars(select(BookSegment).where(BookSegment.book_id == current_id))
        )


@pytest.mark.parametrize("problem", ["invalid", "oversize", "timeout"])
def test_generation_failure_retains_accepted_set_and_version(client, summary_book, monkeypatch, problem):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    snapshot = summaries.start_summary_run(factory, book_id, "test-model")
    version = summaries.publish_book_summaries(factory, snapshot, create_summary_drafts(snapshot))
    calls = []

    async def fail_generation(settings, instruction, parts, output_type, **options):
        calls.append(parts)
        if problem == "invalid" and len(calls) == 1:
            return output_type(
                summaries=[
                    GeneratedSummary(
                        kind=target["kind"],
                        id=target["id"],
                        summary="A staged replacement summary.",
                        english_translation="A replacement translation." if target["kind"] == "segment" else None,
                    )
                    for target in json.loads(parts[1])["TARGETS"]
                ]
            )
        raise {"invalid": ValueError, "oversize": BookTooLargeError, "timeout": TimeoutError}[problem]()

    monkeypatch.setitem(summaries.__dict__, "generate_structured_output", fail_generation)

    with pytest.raises(ValueError, match="exceeds" if problem == "oversize" else "generation failed"):
        asyncio.run(summaries.generate_book_summaries(factory, client.app.state.settings, book_id))

    assert len(calls) == {"oversize": 1, "timeout": 2, "invalid": 3}[problem]
    with factory() as db:
        book = db.get(Book, book_id)
        assert book.content_version == version
        assert book.summary == "The book describes a return home."
        assert db.scalar(select(BookSegment.english_translation)) == "He came home to see his mother."
        accepted = db.get(SummaryGenerationRun, snapshot.run_id)
        assert (accepted.model, accepted.policy, accepted.source_language, accepted.state) == (
            "test-model",
            POLICY,
            "kn",
            "completed",
        )
        failed = db.scalar(select(SummaryGenerationRun).where(SummaryGenerationRun.state == "failed"))
        assert failed.finished_at is not None
        assert failed.failure_reason == "Summary generation or publication failed"


def test_running_job_skips_provider_work_even_if_source_changes(client, summary_book, monkeypatch):
    book_id, version = summary_book
    factory = client.app.state.session_factory
    original = summaries.start_summary_run(factory, book_id, "test-model")
    with factory() as db:
        patch_book(db, book_id, BookPatch(title="A changed source"))

    def reject_provider_work(*args, **kwargs):
        pytest.fail("An active job must be checked before any provider work")

    monkeypatch.setitem(summaries.__dict__, "create_summary_context_cache", reject_provider_work)
    monkeypatch.setitem(summaries.__dict__, "generate_structured_output", reject_provider_work)

    result = asyncio.run(summaries.generate_book_summaries(factory, client.app.state.settings, book_id))

    assert result == version
    with factory() as db:
        assert db.get(SummaryGenerationRun, original.run_id).state == "running"


def test_cleared_run_stops_before_another_provider_request(client, summary_book, monkeypatch):
    book_id, version = summary_book
    factory = client.app.state.session_factory
    calls = []

    async def generate_output(settings, instruction, parts, output_type, **options):
        calls.append(parts)
        summaries.clear_book_summaries(factory, book_id)
        return output_type(
            summaries=[
                GeneratedSummary(
                    kind=target["kind"],
                    id=target["id"],
                    summary="A traveller returns home.",
                    english_translation="He came home to see his mother." if target["kind"] == "segment" else None,
                )
                for target in json.loads(parts[1])["TARGETS"]
            ]
        )

    monkeypatch.setitem(summaries.__dict__, "generate_structured_output", generate_output)

    with pytest.raises(ValueError, match="run changed"):
        asyncio.run(summaries.generate_book_summaries(factory, client.app.state.settings, book_id))

    assert len(calls) == 1
    with factory() as db:
        assert db.get(Book, book_id).content_version == version
        assert db.get(Book, book_id).summary is None
        assert db.scalar(select(SummaryGenerationRun)).state == "failed"


def test_summary_generation_succeeds_with_only_batch_key(client, summary_book, monkeypatch):
    book_id, version = summary_book
    factory = client.app.state.session_factory
    client.app.state.settings.gemini_api_key = None
    client.app.state.settings.gemini_batch_api_key = SecretStr("only-batch-secret")

    async def generate_output(settings, instruction, parts, output_type, **options):
        assert options.get("api_key") == SecretStr("only-batch-secret")
        return output_type(
            summaries=[
                GeneratedSummary(
                    kind=target["kind"],
                    id=target["id"],
                    summary="A traveller returns home.",
                    english_translation="He came home to see his mother." if target["kind"] == "segment" else None,
                )
                for target in json.loads(parts[1])["TARGETS"]
            ]
        )

    monkeypatch.setitem(summaries.__dict__, "generate_structured_output", generate_output)

    new_version = asyncio.run(summaries.generate_book_summaries(factory, client.app.state.settings, book_id))
    assert new_version != version
    with factory() as db:
        assert db.get(Book, book_id).summary == "A traveller returns home."


def test_summary_generation_fails_when_both_keys_are_missing(client, summary_book):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    client.app.state.settings.gemini_api_key = None
    client.app.state.settings.gemini_batch_api_key = None

    with pytest.raises(ValueError, match="generation failed"):
        asyncio.run(summaries.generate_book_summaries(factory, client.app.state.settings, book_id))
