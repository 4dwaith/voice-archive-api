"""Bounded Gemini SDK calls, including counting the complete generation request."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from google import genai
from google.genai import types
from pydantic import BaseModel, SecretStr

from voice_archive_api.core.config import Settings
from voice_archive_api.logic.library import (
    ANSWER_INSTRUCTIONS,
    AnswerDraft,
    EvidenceSegment,
    format_book_evidence,
    format_question,
)
from voice_archive_api.logic.search.dense import validate_query_vector

logger = logging.getLogger(__name__)


class BookTooLargeError(ValueError):
    pass


async def embed_query(settings: Settings, query: str) -> list[float]:
    if (
        settings.gemini_embedding_model != "gemini-embedding-2"
        or settings.embedding_input_format != "retrieval-document-v1"
    ):
        raise ValueError("Unsupported query/document profile")
    key = settings.gemini_api_key
    if not key or not key.get_secret_value():
        raise ValueError("Missing query embedding credential")
    content = f"task: search result | query: {query}"
    # Bound UTF-8 bytes conservatively below the model's 8,192-token limit.
    if len(content.encode("utf-8")) + 16 > 8192:
        raise BookTooLargeError
    with genai.Client(
        api_key=key.get_secret_value(),
        http_options=types.HttpOptions(
            api_version="v1beta",
            timeout=settings.gemini_answer_timeout_seconds * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    ) as client:
        async with client.aio as async_client:
            response = await async_client.models.embed_content(
                model=settings.gemini_embedding_model,
                contents=content,
                config=types.EmbedContentConfig(output_dimensionality=settings.gemini_embedding_dimensions),
            )
    if not response.embeddings or len(response.embeddings) != 1 or response.embeddings[0].values is None:
        raise ValueError("Missing query embedding")
    vector = response.embeddings[0].values
    validate_query_vector(vector, settings.gemini_embedding_dimensions)
    return vector


async def generate_answer(settings: Settings, query: str, evidence: list[EvidenceSegment], effort: str) -> AnswerDraft:
    return await generate_structured_output(
        settings,
        ANSWER_INSTRUCTIONS,
        [format_book_evidence(evidence), format_question(query)],
        AnswerDraft,
        effort,
        reserve_output_tokens=True,
    )


async def generate_structured_output[Output: BaseModel](
    settings: Settings,
    instruction: str,
    parts: list[str],
    output_type: type[Output],
    effort: str = "low",
    *,
    api_key: SecretStr | None = None,
    max_output_tokens: int | None = 8192,
    cached_content: str | None = None,
    report_usage: Callable[[types.GenerateContentResponseUsageMetadata | None], Awaitable[None]] | None = None,
    reserve_output_tokens: bool = False,
) -> Output:
    target_key = api_key or settings.gemini_api_key
    if not target_key or not target_key.get_secret_value():
        raise ValueError("Missing provider configuration")
    model = settings.gemini_text_model
    instructions = types.Content(parts=[types.Part(text=instruction)])
    contents = [
        types.Content(
            role="user",
            parts=[types.Part(text=part) for part in parts],
        )
    ]
    config = types.GenerateContentConfig(
        system_instruction=None if cached_content else instructions,
        cached_content=cached_content,
        response_mime_type="application/json",
        response_json_schema=output_type.model_json_schema(),
        max_output_tokens=max_output_tokens,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel(effort.upper())),
    )
    async with asyncio.timeout(settings.gemini_answer_timeout_seconds):
        with genai.Client(
            api_key=target_key.get_secret_value(),
            http_options=types.HttpOptions(
                api_version="v1beta",
                timeout=settings.gemini_answer_timeout_seconds * 1000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        ) as client:
            async with client.aio as async_client:
                metadata = await async_client.models.get(model=model)
                if max_output_tokens is None:
                    if metadata.output_token_limit is None or metadata.output_token_limit <= 0:
                        raise ValueError("Missing model output limit")
                    config.max_output_tokens = metadata.output_token_limit
                # Count the same complete configuration used for generation.
                # The Developer API accepts it through the SDK's extra_body extension.
                payload = {
                    "model": f"models/{model}",
                    "contents": [
                        content.model_dump(mode="json", by_alias=True, exclude_none=True) for content in contents
                    ],
                    "generationConfig": config.model_dump(
                        mode="json", by_alias=True, exclude_none=True, exclude={"system_instruction", "cached_content"}
                    ),
                }
                if cached_content:
                    payload["cachedContent"] = cached_content
                else:
                    payload["systemInstruction"] = instructions.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    )
                counted = await async_client.models.count_tokens(
                    model=model,
                    contents=contents,
                    config=types.CountTokensConfig(
                        http_options=types.HttpOptions(
                            # CountTokens permits contents OR generateContentRequest.
                            # An empty repeated field leaves top-level contents unset.
                            extra_body={"contents": [], "generateContentRequest": payload},
                        )
                    ),
                )
                input_limit, total = metadata.input_token_limit, counted.total_tokens
                if input_limit is None or total is None or input_limit <= 0 or total < 0:
                    raise ValueError("Missing model limits or token count")
                reserve = (config.max_output_tokens or 0) if reserve_output_tokens else 0
                if total > input_limit or total + reserve > settings.gemini_answer_input_tokens:
                    raise BookTooLargeError
                generated = await async_client.models.generate_content(model=model, contents=contents, config=config)
                if report_usage is not None:
                    await report_usage(generated.usage_metadata)
                logger.debug(
                    "Complete model response: model=%s output_type=%s response=%s",
                    model,
                    output_type.__name__,
                    generated.model_dump_json(),
                )
                candidates = generated.candidates or []
                if len(candidates) != 1 or candidates[0].finish_reason != types.FinishReason.STOP:
                    raise ValueError("Incomplete or blocked answer")
                return output_type.model_validate_json(generated.text or "")
