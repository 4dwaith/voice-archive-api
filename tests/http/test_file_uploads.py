"""Exercise multipart spooling, receive bounds, and cleanup at the HTTP boundary."""

import asyncio

import pytest
from starlette import formparsers

from tests.file_processing.file_samples import pdf_bytes


@pytest.fixture
def tracked_spools(monkeypatch):
    original = formparsers.SpooledTemporaryFile
    spools = []

    def track_spool(*args, **kwargs):
        spool = original(*args, **kwargs)
        spools.append(spool)
        return spool

    # Replace the imported factory in Starlette's module namespace.
    monkeypatch.setitem(vars(formparsers), "SpooledTemporaryFile", track_spool)
    return spools


@pytest.mark.parametrize("valid", [True, False], ids=["success", "invalid-pdf"])
def test_disk_spooled_upload_is_closed_on_success_and_failure(client, file_storage, tracked_spools, valid):
    size = formparsers.MultiPartParser.spool_max_size + 1
    content = pdf_bytes(metadata_size=size) if valid else b"x" * size
    response = client.post("/api/v1/files/source_pdf", files={"file": ("source.pdf", content)})
    assert response.status_code == (201 if valid else 422), response.text
    assert len(tracked_spools) == 1
    assert tracked_spools[0]._rolled is True
    assert tracked_spools[0].closed
    if not valid:
        assert not any(path.is_file() for path in file_storage.root.rglob("*"))


def test_receive_limit_stops_a_chunked_upload_and_closes_partial_spool(client, tracked_spools):
    app = client.app
    app.state.settings.pdf_max_bytes = 1_100_000
    chunks = [
        b'--upload\r\nContent-Disposition: form-data; name="file"; filename="source.pdf"\r\n'
        b"Content-Type: application/pdf\r\n\r\n" + b"x" * (1024 * 1024 + 1),
        b"x" * 200_000,
        b"\r\n--upload--\r\n",
    ]
    received = 0
    sent = []

    async def receive():
        nonlocal received
        body = chunks[received]
        received += 1
        return {"type": "http.request", "body": body, "more_body": received < len(chunks)}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/files/source_pdf",
        "raw_path": b"/api/v1/files/source_pdf",
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"content-type", b"multipart/form-data; boundary=upload"),
            (b"origin", b"http://localhost:5173"),
            (b"cookie", client.build_request("POST", "/api/v1/files/source_pdf").headers["cookie"].encode()),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    assert b'"file_too_large"' in sent[1]["body"]
    assert received == 2  # The request is rejected without consuming the remaining body.
    assert len(tracked_spools) == 1
    assert tracked_spools[0]._rolled is True
    assert tracked_spools[0].closed


def test_configured_total_request_limit_is_applied_before_service(client):
    client.app.state.settings.upload_max_bytes = 32
    response = client.post("/api/v1/files/cover", files={"file": ("cover.png", b"x" * 33)})
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "file_too_large"


def test_unknown_kind_is_rejected_before_reading_or_spooling(client, tracked_spools):
    response = client.post("/api/v1/files/unknown", files={"file": ("file.bin", b"data")})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["issues"][0]["location"] == ["path", "kind"]
    assert tracked_spools == []


def test_kind_selects_its_own_upload_limit(client, application):
    payload = pdf_bytes()
    application.state.settings.cover_max_bytes = 1
    application.state.settings.pdf_max_bytes = len(payload)
    accepted = client.post("/api/v1/files/source_pdf", files={"file": ("source.pdf", payload)})
    assert accepted.status_code == 201
    assert accepted.json()["kind"] == "source_pdf"
    application.state.settings.pdf_max_bytes -= 1
    rejected = client.post("/api/v1/files/source_pdf", files={"file": ("source.pdf", payload)})
    assert rejected.status_code == 413
