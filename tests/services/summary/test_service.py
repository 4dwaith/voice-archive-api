"""Provider batching, cache retry and generation lifecycle orchestration."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from google.genai import Client, types
from pydantic import SecretStr
from sqlalchemy import select

from tests.summary_helpers import create_summary_drafts
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSegment
from voice_archive_api.db.models.summary import SummaryBatchCheckpoint, SummaryGenerationRun
from voice_archive_api.logic.summaries import POLICY, SUMMARY_INSTRUCTIONS, GeneratedSummary
from voice_archive_api.services.books.schemas import BookPatch
from voice_archive_api.services.books.service import patch_book
from voice_archive_api.services.library.provider import BookTooLargeError
from voice_archive_api.services.summary import service as summaries


@pytest.mark.parametrize("cached_request_fails", [False, True])
@pytest.mark.parametrize("effort", ["low", "high"])
def test_batched_roots_keep_full_context_and_carry_earlier_summaries(
    client, summary_series, monkeypatch, cached_request_fails, effort
):
    _, _, current_id = summary_series
    requests = []
    cache_events = []
    cached_source = None
    generation_attempts = 0
    reported_usage = []
    client.app.state.settings.gemini_batch_api_key = SecretStr("provider-batch-secret")
    client.app.state.settings.gemini_api_key = SecretStr("provider-interactive-secret")

    async def respond(request):
        nonlocal cached_source, generation_attempts
        payload = json.loads(request.content) if request.content else {}
        if request.url.path.endswith(":countTokens"):
            if "generateContentRequest" in payload:
                counted = payload["generateContentRequest"]
                config = types.GenerateContentConfig.model_validate(counted["generationConfig"])
                assert config.thinking_config.thinking_level == types.ThinkingLevel(effort.upper())
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
        config = types.GenerateContentConfig.model_validate(payload["generationConfig"])
        assert config.thinking_config.thinking_level == types.ThinkingLevel(effort.upper())
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
        usage = {
            "promptTokenCount": 8000,
            "cachedContentTokenCount": 7000 if payload.get("cachedContent") else 0,
            "candidatesTokenCount": 100,
            "thoughtsTokenCount": 50,
            "totalTokenCount": 8150,
        }
        reported_usage.append(usage)
        return httpx.Response(
            200,
            json={
                "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(result)}]}}],
                "usageMetadata": usage,
            },
        )

    def create_test_client(*, api_key, http_options):
        assert api_key == "provider-batch-secret"
        http_options.async_client_args = {"transport": httpx.MockTransport(respond)}
        return Client(api_key=api_key, http_options=http_options)

    monkeypatch.setattr("voice_archive_api.services.library.provider.genai.Client", create_test_client)
    monkeypatch.setattr("voice_archive_api.services.summary.cache.genai.Client", create_test_client)

    asyncio.run(
        summaries.generate_book_summaries(
            client.app.state.session_factory, client.app.state.settings, current_id, effort=effort
        )
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
        run = db.scalar(select(SummaryGenerationRun).where(SummaryGenerationRun.book_id == current_id))
        assert run.input_tokens == 32000
        assert run.cached_input_tokens == sum(usage["cachedContentTokenCount"] for usage in reported_usage)
        assert (run.output_tokens, run.thinking_tokens, run.total_tokens) == (400, 200, 32600)
        assert (run.generation_response_count, run.missing_usage_response_count) == (4, 0)


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


def test_records_rejected_response_usage_before_successful_retry(client, summary_roots, monkeypatch):
    book_id, roots = summary_roots
    factory = client.app.state.session_factory
    client.app.state.settings.gemini_batch_api_key = SecretStr("test-key")
    responses = 0

    def respond(request):
        nonlocal responses
        if request.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 100})
        if not request.url.path.endswith(":generateContent"):
            return httpx.Response(200, json={"inputTokenLimit": 10000, "outputTokenLimit": 10000})
        responses += 1
        payload = json.loads(request.content)
        targets = json.loads(payload["contents"][0]["parts"][1]["text"])["TARGETS"]
        output = {
            "summaries": [
                {
                    "kind": target["kind"],
                    "id": target["id"],
                    "summary": "The traveller returns home.",
                    "english_translation": "First." if target["kind"] == "segment" else None,
                }
                for target in targets
            ]
        }
        response_text = "invalid JSON" if responses == 1 else json.dumps(output)
        return httpx.Response(
            200,
            json={
                "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": response_text}]}}],
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 20,
                    "thoughtsTokenCount": 50,
                    "totalTokenCount": 170,
                },
            },
        )

    def create_test_client(*, api_key, http_options):
        http_options.async_client_args = {"transport": httpx.MockTransport(respond)}
        return Client(api_key=api_key, http_options=http_options)

    monkeypatch.setattr("voice_archive_api.services.library.provider.genai.Client", create_test_client)

    asyncio.run(
        summaries.generate_book_summaries(
            factory, client.app.state.settings, book_id, root_section_id=roots[0], retries=1, effort="high"
        )
    )

    assert responses == 2
    with factory() as db:
        run = db.scalar(select(SummaryGenerationRun))

        assert run.state == "completed"
        assert run.input_tokens == 200
        assert run.cached_input_tokens == 0
        assert run.output_tokens == 40
        assert run.thinking_tokens == 100
        assert run.total_tokens == 340
        assert run.generation_response_count == 2
        assert run.missing_usage_response_count == 0


def test_summary_generation_fails_when_both_keys_are_missing(client, summary_book):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    client.app.state.settings.gemini_api_key = None
    client.app.state.settings.gemini_batch_api_key = None

    with pytest.raises(ValueError, match="generation failed"):
        asyncio.run(summaries.generate_book_summaries(factory, client.app.state.settings, book_id))


def test_resumes_validated_batches_without_rebilling_them(client, summary_series, monkeypatch):
    _, _, book_id = summary_series
    factory = client.app.state.session_factory
    requests = []
    should_fail = True
    usage = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=100, candidates_token_count=20, thoughts_token_count=10, total_token_count=130
    )

    async def generate_output(settings, instruction, parts, output_type, **options):
        targets = json.loads(parts[-1])["TARGETS"]
        requests.append(targets)
        await options["report_usage"](usage)
        if should_fail and len(requests) == 2:
            return output_type(summaries=[])
        return output_type(
            summaries=[
                {"kind": target["kind"], "id": target["id"], "summary": "A traveller returns home."}
                for target in targets
            ]
        )

    monkeypatch.setattr(summaries, "generate_structured_output", generate_output)
    with factory() as db:
        original_version = db.get(Book, book_id).content_version

    with pytest.raises(ValueError, match="generation failed"):
        asyncio.run(summaries.generate_book_summaries(factory, client.app.state.settings, book_id, retries=0))

    with factory() as db:
        failed_run = db.scalar(select(SummaryGenerationRun).where(SummaryGenerationRun.book_id == book_id))

        assert failed_run.state == "failed"
        assert failed_run.generation_response_count == 2
        assert failed_run.total_tokens == 260
        assert (
            len(db.scalars(select(SummaryBatchCheckpoint).where(SummaryBatchCheckpoint.run_id == failed_run.id)).all())
            == 1
        )
        assert db.get(Book, book_id).content_version == original_version
        assert all(row.summary is None for row in db.scalars(select(BookSegment).where(BookSegment.book_id == book_id)))

    failed_targets = requests[-1]
    requests.clear()

    with pytest.raises(ValueError, match="generation failed"):
        asyncio.run(summaries.generate_book_summaries(factory, client.app.state.settings, book_id, retries=0))

    assert requests[0] == failed_targets
    with factory() as db:
        second_failed_run = db.scalar(
            select(SummaryGenerationRun).where(
                SummaryGenerationRun.book_id == book_id, SummaryGenerationRun.id != failed_run.id
            )
        )

        assert second_failed_run.state == "failed"
        assert second_failed_run.generation_response_count == 2
        assert second_failed_run.total_tokens == 260
        assert db.get(Book, book_id).content_version == original_version

    failed_targets = requests[-1]
    requests.clear()
    should_fail = False
    progress = []

    new_version = asyncio.run(
        summaries.generate_book_summaries(
            factory, client.app.state.settings, book_id, retries=0, report_progress=progress.append
        )
    )

    assert new_version != original_version
    assert len(requests) == 2
    assert requests[0] == failed_targets
    assert sum("reused checkpoint" in message for message in progress) == 2
    with factory() as db:
        completed_run = db.scalar(
            select(SummaryGenerationRun).where(
                SummaryGenerationRun.book_id == book_id, SummaryGenerationRun.state == "completed"
            )
        )

        assert completed_run.id != failed_run.id
        assert completed_run.generation_response_count == 2
        assert completed_run.total_tokens == 260
        assert db.get(SummaryGenerationRun, failed_run.id).total_tokens == 260
        assert db.get(SummaryGenerationRun, second_failed_run.id).total_tokens == 260
        assert all(row.summary for row in db.scalars(select(BookSegment).where(BookSegment.book_id == book_id)))


@pytest.mark.parametrize("change_prompt", [False, True])
def test_publication_failure_resumes_only_identical_requests(client, summary_book, monkeypatch, change_prompt):
    book_id, original_version = summary_book
    factory = client.app.state.session_factory
    calls = 0
    publish = summaries.publish_book_summaries

    async def generate_output(settings, instruction, parts, output_type, **options):
        nonlocal calls
        calls += 1
        return output_type(
            summaries=[
                {
                    "kind": target["kind"],
                    "id": target["id"],
                    "summary": "A traveller returns home.",
                    "english_translation": "He came home to see his mother." if target["kind"] == "segment" else None,
                }
                for target in json.loads(parts[-1])["TARGETS"]
            ]
        )

    def fail_publication(*args):
        raise RuntimeError("Publication interrupted")

    monkeypatch.setattr(summaries, "generate_structured_output", generate_output)
    monkeypatch.setattr(summaries, "publish_book_summaries", fail_publication)

    with pytest.raises(RuntimeError, match="Publication interrupted"):
        asyncio.run(summaries.generate_book_summaries(factory, client.app.state.settings, book_id))

    assert calls == 2
    with factory() as db:
        assert db.get(Book, book_id).content_version == original_version
        assert db.get(Book, book_id).summary is None

    monkeypatch.setattr(summaries, "publish_book_summaries", publish)
    if change_prompt:
        monkeypatch.setattr(summaries, "SUMMARY_INSTRUCTIONS", SUMMARY_INSTRUCTIONS + " Preserve source phrasing.")

    new_version = asyncio.run(summaries.generate_book_summaries(factory, client.app.state.settings, book_id))

    assert calls == (4 if change_prompt else 2)
    assert new_version != original_version
    with factory() as db:
        assert db.get(Book, book_id).summary == "A traveller returns home."
