"""Application wiring around native file delivery; range mechanics belong to Starlette."""

from io import BytesIO
from unittest.mock import Mock

import pytest
from fastapi.responses import FileResponse

from voice_archive_api.book_files import service
from voice_archive_api.core.dependencies import get_file_delivery
from voice_archive_api.db.session import get_db
from voice_archive_api.file_handling import LocalStorage
from voice_archive_api.file_handling.local_file_delivery import LocalFileDelivery


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_database_session_is_released_before_file_delivery(schema_only_client, monkeypatch, tmp_path, method):
    state = {"session_closed": False}

    def database():
        try:
            yield Mock()
        finally:
            state["session_closed"] = True

    storage = LocalStorage(tmp_path / "uploads")
    key = storage.promote(storage.put_temporary(BytesIO(b"test PDF bytes"), max_bytes=100))
    record = Mock(storage_key=key, kind="source_pdf")
    monkeypatch.setattr(service, "get_file", Mock(return_value=record))
    overrides = schema_only_client.app.dependency_overrides
    monkeypatch.setitem(overrides, get_db, database)
    monkeypatch.setitem(overrides, get_file_delivery, lambda: LocalFileDelivery(storage))

    original_call = FileResponse.__call__

    async def check_session_closed(response, scope, receive, send):
        assert state["session_closed"]
        await original_call(response, scope, receive, send)

    monkeypatch.setattr(FileResponse, "__call__", check_session_closed)
    response = schema_only_client.request(method, "/api/v1/files/1")
    assert response.status_code == 200
    assert response.content == (b"test PDF bytes" if method == "GET" else b"")


def test_delivery_rejects_a_directory_instead_of_a_file(tmp_path):
    storage = LocalStorage(tmp_path / "uploads")
    key = "promoted/" + "a" * 32
    storage.local_path(key).mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        LocalFileDelivery(storage).response(key, media_type="application/pdf", filename="source.pdf")
