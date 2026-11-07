"""Bounded Gemini SDK calls, including counting the complete generation request."""

import asyncio

from google import genai
from google.genai import types
from pydantic import BaseModel

from voice_archive_api.core.config import Settings
from voice_archive_api.logic.library import (
    ANSWER_INSTRUCTIONS,
    AnswerDraft,
    EvidenceSegment,
    format_book_evidence,
    format_question,
)


class BookTooLargeError(ValueError):
    pass


async def generate_answer(settings: Settings, query: str, evidence: list[EvidenceSegment], effort: str) -> AnswerDraft:
    return await generate_structured_output(
        settings, ANSWER_INSTRUCTIONS, [format_book_evidence(evidence), format_question(query)], AnswerDraft, effort
    )


async def generate_structured_output[Output: BaseModel](
    settings: Settings,
    instruction: str,
    parts: list[str],
    output_type: type[Output],
    effort: str = "low",
    *,
    max_output_tokens: int | None = 8192,
    cached_content: str | None = None,
) -> Output:
    if not settings.gemini_api_key or not settings.gemini_api_key.get_secret_value():
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
            api_key=settings.gemini_api_key.get_secret_value(),
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
                if total > min(input_limit, settings.gemini_answer_input_tokens):
                    raise BookTooLargeError
                generated = await async_client.models.generate_content(model=model, contents=contents, config=config)
                candidates = generated.candidates or []
                if len(candidates) != 1 or candidates[0].finish_reason != types.FinishReason.STOP:
                    raise ValueError("Incomplete or blocked answer")
                return output_type.model_validate_json(generated.text or "")
