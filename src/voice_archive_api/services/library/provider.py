"""Bounded Gemini SDK calls, including counting the complete generation request."""

import asyncio

from google import genai
from google.genai import types

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
    if not settings.gemini_api_key or not settings.gemini_api_key.get_secret_value():
        raise ValueError("Missing provider configuration")
    model = settings.gemini_text_model
    instructions = types.Content(parts=[types.Part(text=ANSWER_INSTRUCTIONS)])
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part(text=format_book_evidence(evidence)),
                types.Part(text=format_question(query)),
            ],
        )
    ]
    config = types.GenerateContentConfig(
        system_instruction=instructions,
        response_mime_type="application/json",
        response_json_schema=AnswerDraft.model_json_schema(),
        max_output_tokens=8192,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel(effort.upper())),
    )
    # The Developer API's typed CountTokensConfig rejects system/generation
    # configuration. Its public extra_body extension supports the complete request.
    payload = {
        "model": f"models/{model}",
        "systemInstruction": instructions.model_dump(mode="json", by_alias=True, exclude_none=True),
        "contents": [content.model_dump(mode="json", by_alias=True, exclude_none=True) for content in contents],
        "generationConfig": config.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude={"system_instruction"}
        ),
    }
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
                return AnswerDraft.model_validate_json(generated.text or "")
