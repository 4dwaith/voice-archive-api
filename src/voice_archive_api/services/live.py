from datetime import UTC, datetime, timedelta

import httpx
from google import genai
from google.genai import errors, types

from voice_archive_api.core.config import Settings
from voice_archive_api.core.errors import ApplicationError


class LiveUnavailableError(ApplicationError):
    code = "live_unavailable"
    message = "Live is unavailable. Check the server Gemini configuration or try again."


async def create_live_token(settings: Settings) -> str:
    if not settings.gemini_api_key or not settings.gemini_api_key.get_secret_value():
        raise LiveUnavailableError
    now = datetime.now(UTC)
    try:
        with genai.Client(
            api_key=settings.gemini_api_key.get_secret_value(),
            http_options=types.HttpOptions(
                api_version="v1beta", timeout=15000, retry_options=types.HttpRetryOptions(attempts=1)
            ),
        ) as client:
            async with client.aio as async_client:
                response = await async_client.auth_tokens.create(
                    config=types.CreateAuthTokenConfig(
                        uses=1,
                        expire_time=now + timedelta(minutes=30),
                        new_session_expire_time=now + timedelta(minutes=1),
                        live_connect_constraints=types.LiveConnectConstraints(
                            model=settings.gemini_live_model,
                            config=types.LiveConnectConfig(response_modalities=[types.Modality.AUDIO]),
                        ),
                    )
                )
                token = response.name
                if not isinstance(token, str) or not token:
                    raise ValueError("Missing token")
    except (errors.APIError, httpx.HTTPError, TimeoutError, ValueError) as error:
        raise LiveUnavailableError from error
    return token
