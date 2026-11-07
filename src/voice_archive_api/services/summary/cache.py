"""One short-lived provider cache for a root's repeated summary context."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from google import genai
from google.genai import errors, types

from voice_archive_api.core.config import Settings

logger = logging.getLogger(__name__)
CACHE_TTL_SECONDS = 1200
CACHE_RENEWAL_MARGIN_SECONDS = 300
MIN_CACHE_TOKENS = 4096  # The configured Gemini 3.8 Flash minimum.


@dataclass
class SummaryCache:
    client: genai.client.AsyncClient
    settings: Settings
    name: str
    expires_at: datetime

    async def get_available_name(self) -> str | None:
        # A whole request must fit within the remaining lifetime, including when
        # an operator raises the normal 90-second provider deadline.
        margin = max(CACHE_RENEWAL_MARGIN_SECONDS, self.settings.gemini_answer_timeout_seconds + 30)
        if (self.expires_at - datetime.now(UTC)).total_seconds() > margin:
            return self.name
        ttl = max(CACHE_TTL_SECONDS, margin + 60)
        try:
            async with asyncio.timeout(self.settings.gemini_answer_timeout_seconds):
                updated = await self.client.caches.update(
                    name=self.name, config=types.UpdateCachedContentConfig(ttl=f"{ttl}s")
                )
            if updated.expire_time is not None:
                self.expires_at = updated.expire_time
                return self.name
        except errors.APIError, httpx.HTTPError, TimeoutError, ValueError:
            pass
        logger.warning("Summary cache renewal unavailable; using canonical context directly")
        return None


@asynccontextmanager
async def create_summary_cache(
    settings: Settings, instruction: str, source: str, *, enabled: bool
) -> AsyncIterator[SummaryCache | None]:
    if not enabled or not settings.gemini_api_key:
        yield None
        return
    cache = None
    with genai.Client(
        api_key=settings.gemini_api_key.get_secret_value(),
        http_options=types.HttpOptions(
            api_version="v1beta",
            timeout=settings.gemini_answer_timeout_seconds * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    ) as client:
        async with client.aio as async_client:
            contents = [types.Content(role="user", parts=[types.Part(text=source)])]
            try:
                async with asyncio.timeout(settings.gemini_answer_timeout_seconds):
                    counted = await async_client.models.count_tokens(
                        model=settings.gemini_text_model,
                        contents=[types.Content(parts=[types.Part(text=instruction)]), *contents],
                    )
                    total = counted.total_tokens
                    if total is not None and MIN_CACHE_TOKENS <= total <= settings.gemini_answer_input_tokens:
                        created = await async_client.caches.create(
                            model=settings.gemini_text_model,
                            config=types.CreateCachedContentConfig(
                                system_instruction=instruction, contents=contents, ttl=f"{CACHE_TTL_SECONDS}s"
                            ),
                        )
                        if created.name and created.expire_time:
                            cache = SummaryCache(async_client, settings, created.name, created.expire_time)
            except errors.APIError, httpx.HTTPError, TimeoutError, ValueError:
                logger.warning("Summary cache preparation unavailable; using canonical context directly")
            try:
                yield cache
            finally:
                if cache is not None:
                    try:
                        async with asyncio.timeout(settings.gemini_answer_timeout_seconds):
                            await async_client.caches.delete(name=cache.name)
                    except errors.APIError, httpx.HTTPError, TimeoutError, ValueError:
                        logger.warning("Summary cache cleanup unavailable; its TTL will expire")
