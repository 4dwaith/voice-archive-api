import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from google.genai import Client, errors, types
from pydantic import SecretStr


@pytest.fixture
def mock_gemini_client(monkeypatch):
    client_class = MagicMock()
    client = client_class.return_value
    client.__enter__.return_value = client
    async_client = client.aio.__aenter__.return_value
    async_client.auth_tokens.create = AsyncMock(return_value=types.AuthToken(name="auth_tokens/temporary"))
    monkeypatch.setattr("voice_archive_api.services.live.genai.Client", client_class)
    return client_class


def test_live_token_is_single_use_constrained_and_not_cached(schema_only_client, mock_gemini_client):
    settings = schema_only_client.app.state.settings
    settings.gemini_api_key = SecretStr("server-secret")
    before = datetime.now(UTC)

    response = schema_only_client.post("/api/v1/live/token")

    after = datetime.now(UTC)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"token": "auth_tokens/temporary", "model": settings.gemini_live_model}
    assert "server-secret" not in response.text
    mock_gemini_client.assert_called_once_with(
        api_key="server-secret",
        http_options=types.HttpOptions(
            api_version="v1beta", timeout=15000, retry_options=types.HttpRetryOptions(attempts=1)
        ),
    )
    client = mock_gemini_client.return_value
    create_token = client.aio.__aenter__.return_value.auth_tokens.create
    create_token.assert_awaited_once()
    config = create_token.call_args.kwargs["config"]
    assert config.uses == 1
    assert config.live_connect_constraints == types.LiveConnectConstraints(
        model=settings.gemini_live_model,
        config=types.LiveConnectConfig(response_modalities=[types.Modality.AUDIO]),
    )
    assert before + timedelta(minutes=1) <= config.new_session_expire_time <= after + timedelta(minutes=1)
    assert config.expire_time - config.new_session_expire_time == timedelta(minutes=29)
    client.aio.__aexit__.assert_awaited_once()
    client.__exit__.assert_called_once()


def test_live_token_locks_only_model_and_audio_in_sdk_request(schema_only_client, monkeypatch):
    schema_only_client.app.state.settings.gemini_api_key = SecretStr("server-secret")
    requests = []

    def handle_request(request):
        requests.append(request)
        return httpx.Response(200, json={"name": "auth_tokens/temporary"})

    def create_test_client(*, api_key, http_options):
        http_options.httpx_async_client = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))
        return Client(api_key=api_key, http_options=http_options)

    monkeypatch.setattr("voice_archive_api.services.live.genai.Client", create_test_client)

    response = schema_only_client.post("/api/v1/live/token")

    assert response.status_code == 200
    assert len(requests) == 1
    assert requests[0].url.path == "/v1beta/auth_tokens"
    payload = json.loads(requests[0].content)
    assert set(payload["fieldMask"].split(",")) == {"model", "generationConfig.responseModalities"}
    assert payload["bidiGenerateContentSetup"] == {
        "model": f"models/{schema_only_client.app.state.settings.gemini_live_model}",
        "generationConfig": {"responseModalities": ["AUDIO"]},
    }


def test_live_without_configuration_is_unavailable(schema_only_client, mock_gemini_client):
    schema_only_client.app.state.settings.gemini_api_key = None
    schema_only_client.app.state.settings.gemini_batch_api_key = SecretStr("batch-only-secret")

    response = schema_only_client.post("/api/v1/live/token")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "live_unavailable"
    mock_gemini_client.assert_not_called()


@pytest.mark.parametrize("failure", ["network", "rejected", "invalid", "timeout"])
def test_live_provider_failures_hide_credentials(schema_only_client, mock_gemini_client, failure):
    schema_only_client.app.state.settings.gemini_api_key = SecretStr("server-secret")
    failures = {
        "network": httpx.ConnectError("server-secret"),
        "rejected": errors.ClientError(403, {"error": {"message": "server-secret"}}),
        "invalid": ValueError("server-secret"),
        "timeout": TimeoutError("server-secret"),
    }
    client = mock_gemini_client.return_value
    client.aio.__aenter__.return_value.auth_tokens.create.side_effect = failures[failure]

    response = schema_only_client.post("/api/v1/live/token")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "live_unavailable"
    assert "server-secret" not in response.text
    client.aio.__aexit__.assert_awaited_once()
    client.__exit__.assert_called_once()


@pytest.mark.parametrize("name", [None, ""])
def test_live_without_a_provider_token_is_unavailable(schema_only_client, mock_gemini_client, name):
    schema_only_client.app.state.settings.gemini_api_key = SecretStr("server-secret")
    create_token = mock_gemini_client.return_value.aio.__aenter__.return_value.auth_tokens.create
    create_token.return_value = types.AuthToken(name=name)

    response = schema_only_client.post("/api/v1/live/token")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "live_unavailable"
