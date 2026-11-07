import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from google.genai import Client
from pydantic import SecretStr

from voice_archive_api.core.config import Settings
from voice_archive_api.services.summary.cache import create_summary_context_cache


@pytest.mark.parametrize(
    "outcome", ["success", "too_small", "create_failure", "renew_failure", "delete_failure", "generation_failure"]
)
def test_cache_lifetime_and_optional_failure_fallback(monkeypatch, outcome):
    settings = Settings(gemini_batch_api_key=SecretStr("batch-secret"))
    methods = []

    async def respond(request):
        payload = json.loads(request.content) if request.content else {}
        if request.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 4095 if outcome == "too_small" else 4096})
        methods.append(request.method)
        if request.method == "POST" and outcome == "create_failure":
            return httpx.Response(503, json={"error": {"code": 503, "message": "Unavailable"}})
        if request.method == "PATCH" and outcome == "renew_failure":
            return httpx.Response(404, json={"error": {"code": 404, "message": "Expired"}})
        if request.method == "DELETE":
            if outcome == "delete_failure":
                return httpx.Response(503, json={"error": {"code": 503, "message": "Unavailable"}})
            return httpx.Response(200, json={})
        assert payload["ttl"] == "1200s"
        minutes = 1 if request.method == "POST" else 20
        return httpx.Response(
            200,
            json={
                "name": "cachedContents/root",
                "expireTime": (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat(),
            },
        )

    def create_test_client(*, api_key, http_options):
        assert api_key == "batch-secret"
        http_options.async_client_args = {"transport": httpx.MockTransport(respond)}
        return Client(api_key=api_key, http_options=http_options)

    monkeypatch.setattr("voice_archive_api.services.summary.cache.genai.Client", create_test_client)

    async def use_cache():
        async with create_summary_context_cache(
            settings, "Summarize faithfully", "Canonical context", enabled=True
        ) as cache:
            if outcome in {"too_small", "create_failure"}:
                assert cache is None
                return
            assert cache is not None

            name = await cache.get_available_name()

            assert name == (None if outcome == "renew_failure" else "cachedContents/root")
            if outcome == "generation_failure":
                raise ValueError("Generation failed")

    if outcome == "generation_failure":
        with pytest.raises(ValueError, match="Generation failed"):
            asyncio.run(use_cache())
    else:
        asyncio.run(use_cache())

    assert methods == (
        [] if outcome == "too_small" else ["POST"] if outcome == "create_failure" else ["POST", "PATCH", "DELETE"]
    )


def test_cache_falls_back_to_interactive_api_key_when_batch_key_unset(monkeypatch):
    settings = Settings(gemini_api_key=SecretStr("interactive-fallback-secret"), gemini_batch_api_key=None)

    def create_test_client(*, api_key, http_options):
        assert api_key == "interactive-fallback-secret"

        async def respond(request):
            return httpx.Response(200, json={"totalTokens": 100})

        http_options.async_client_args = {"transport": httpx.MockTransport(respond)}
        return Client(api_key=api_key, http_options=http_options)

    monkeypatch.setattr("voice_archive_api.services.summary.cache.genai.Client", create_test_client)

    async def use_cache():
        async with create_summary_context_cache(settings, "Instruction", "Source", enabled=True) as cache:
            assert cache is None

    asyncio.run(use_cache())


def test_cache_without_any_key_skips_client_creation(monkeypatch):
    settings = Settings(gemini_api_key=None, gemini_batch_api_key=None)

    def reject_client_creation(**kwargs):
        pytest.fail("Missing keys must not instantiate Client")

    monkeypatch.setattr("voice_archive_api.services.summary.cache.genai.Client", reject_client_creation)

    async def use_cache():
        async with create_summary_context_cache(settings, "Instruction", "Source", enabled=True) as cache:
            assert cache is None

    asyncio.run(use_cache())


def test_single_request_scope_skips_all_cache_calls(monkeypatch):
    settings = Settings(gemini_api_key=SecretStr("test-secret"))

    def reject_client_creation(**kwargs):
        pytest.fail("A single request must not prepare a cache")

    monkeypatch.setattr("voice_archive_api.services.summary.cache.genai.Client", reject_client_creation)

    async def use_cache():
        async with create_summary_context_cache(settings, "Instruction", "Source", enabled=False) as cache:
            assert cache is None

    asyncio.run(use_cache())
