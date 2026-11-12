import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from voice_archive_api.core.config import Settings
from voice_archive_api.main import create_app


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"Origin": "http://localhost:5173"}, 200),
        ({"Origin": "http://localhost:5173", "Sec-Fetch-Site": "cross-site"}, 200),
        ({"Origin": "https://attacker.example", "Sec-Fetch-Site": "same-origin"}, 403),
        ({"Origin": "null"}, 403),
        ({"Referer": "http://localhost:5173/book"}, 200),
        ({"Referer": "http://localhost:5173.attacker.example/book"}, 403),
        ({"Sec-Fetch-Site": "same-origin"}, 200),
        ({"Sec-Fetch-Site": "same-site"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({"Sec-Fetch-Site": "none"}, 403),
        ({}, 403),
    ],
)
def test_origin_policy_precedes_request_body_processing(headers, expected):
    app = create_app(Settings(postgres_user="unused", postgres_password="unused", postgres_db="unused", _env_file=None))
    processed = []

    @app.post("/api/probe")
    async def record_processing(request: Request):
        processed.append(await request.body())
        return {"ok": True}

    with TestClient(app) as client:
        response = client.post(
            "/api/probe", content=b"multipart or bodyless writes also need origin checks", headers=headers
        )

    assert response.status_code == expected
    assert len(processed) == int(expected == 200)
