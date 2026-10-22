"""Application wiring around native file delivery; range mechanics belong to Starlette."""

from io import BytesIO
from unittest.mock import Mock

import pytest
from fastapi.responses import FileResponse

from tests.database.content_helpers import add_audio, add_section
from tests.database.files.test_file_ownership import add_book
from voice_archive_api.core.dependencies import get_file_delivery
from voice_archive_api.db.session import get_db
from voice_archive_api.file_handling import LocalStorage
from voice_archive_api.file_handling.local_file_delivery import LocalFileDelivery
from voice_archive_api.logic.files import FileKind
from voice_archive_api.services.files import service
from voice_archive_api.services.files.audio import InvalidAudio


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


def test_section_file_http_delete_guard_and_invalid_audio_upload(client, application):
    with application.state.session_factory() as db:
        root = add_section(db, add_book(db).id)
        record = add_audio(db, section_id=root.id)
        file_id = record.id
        db.commit()
    response = client.delete(f"/api/v1/files/{file_id}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "file_already_linked"
    assert (
        client.post("/api/v1/files/audio_chapter", files={"file": ("chapter.mp3", b"bytes", "audio/mpeg")}).status_code
        == 422
    )


def test_audio_registry_delivery_and_invalid_preparation(client, application, file_storage):
    from io import BytesIO

    from voice_archive_api.services.files.service import create_file

    data = b"synthetic invalid MP3 bytes"
    key = file_storage.promote(file_storage.put_temporary(BytesIO(data), max_bytes=1000))
    with application.state.session_factory() as db:
        record = add_audio(db, storage_key=key, byte_size=len(data))
        file_id = record.id
        db.commit()
        with pytest.raises(InvalidAudio):
            create_file(db, FileKind.AUDIO_CHAPTER, BytesIO(data), file_storage, max_bytes=1000)
    response = client.get(f"/api/v1/files/{file_id}", headers={"Range": "bytes=0-8"})
    assert response.status_code == 206
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == data[:9]
    assert client.head(f"/api/v1/files/{file_id}").status_code == 200
