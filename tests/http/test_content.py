"""Real PostgreSQL publication/purge through the HTTP contract."""

import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.file_processing.file_samples import cover_bytes, mp3_bytes, pdf_bytes
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.file import PendingFileDeletion, StoredFile
from voice_archive_api.logic.content_validation.validation import validate_content
from voice_archive_api.services.content import service


def upload(client, kind, content):
    response = client.post(f"/api/v1/files/{kind}", files={"file": ("ignored-name.bin", content)})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def setup_book(client, *, title="Import", attach_pdf=False):
    pdf = upload(client, "source_pdf", pdf_bytes())
    root_audio = upload(client, "audio_chapter", mp3_bytes())
    nested_audio = upload(client, "audio_chapter", mp3_bytes())
    cover = upload(client, "cover", cover_bytes())
    book = client.post(
        "/api/v1/books",
        json={
            "title": title,
            "language": "en",
            "cover_file_id": cover,
            **({"source_pdf_file_id": pdf} if attach_pdf else {}),
        },
    ).json()["id"]
    data = {
        "schema_version": 1,
        "source_pdf_file_id": pdf,
        # Deliberately child before parent; document order is explicit.
        "sections": [
            {
                "source_key": "child",
                "parent_source_key": "root",
                "kind": "section",
                "title": "Nested",
                "sibling_sequence": 0,
                "audio_file_id": nested_audio,
            },
            {
                "source_key": "root",
                "kind": "chapter",
                "title": "Root",
                "sibling_sequence": 0,
                "audio_file_id": root_audio,
            },
        ],
        "segments": [
            {
                "source_key": "a",
                "section_source_key": "child",
                "sequence": 0,
                "kind": "prose",
                "html_content": "<p><em>Hello</em><br>world</p>",
                "regions": [{"page_index": 0, "x0": 0.1, "y0": 0.2, "x1": 0.4, "y1": 0.5}],
                "narration_start_ms": 500,
                "narration_end_ms": 900,
                "narration_seek_ms": 400,
            },
            {
                "source_key": "b",
                "section_source_key": "root",
                "sequence": 2,
                "kind": "verse",
                "html_content": "<p>Earlier</p>",
                "narration_start_ms": 100,
                "narration_end_ms": 200,
            },
        ],
    }
    return book, data, (pdf, root_audio, nested_audio), cover


def test_import_dry_run_publish_repeat_purge_and_fresh_version(client, test_engine, file_storage):
    book, data, media, cover = setup_book(client, attach_pdf=True)
    url = f"/api/v1/books/{book}"
    before = client.get(url + "/content")
    dry = client.post(url + "/content-imports?dry_run=true", json=data)
    assert dry.status_code == 200, dry.text
    assert dry.json() == {"dry_run": True, "section_count": 2, "segment_count": 2, "content_version": None}
    assert client.get(url + "/content").headers["etag"] == before.headers["etag"]
    with Session(test_engine) as db:
        assert db.scalar(select(func.count()).select_from(BookSection)) == 0
        assert db.get(StoredFile, media[1]).section_id is None
    result = client.post(url + "/content-imports", json=data)
    assert result.status_code == 200, result.text
    manifest = client.get(url + "/content")
    assert manifest.json()["state"] == "published"
    assert manifest.json()["content_version"] == result.json()["content_version"]
    for suffix in ("", "?dry_run=true"):
        assert client.post(url + "/content-imports" + suffix, json=data).status_code == 409
    with Session(test_engine) as db:
        segment = db.scalar(select(BookSegment).where(BookSegment.source_key == "a"))
        assert segment.text_content == "Hello\nworld"
        assert segment.page_indexes == [0]
        root = db.scalar(select(BookSection).where(BookSection.source_key == "root"))
        assert db.get(StoredFile, media[1]).section_id == root.id
        keys = [db.get(StoredFile, file_id).storage_key for file_id in media]
    assert client.delete(f"/api/v1/files/{media[2]}").status_code == 409
    assert client.delete(url + "/content").status_code == 428
    for tag in (before.headers["etag"], "*", "W/" + manifest.headers["etag"]):
        assert client.delete(url + "/content", headers={"If-Match": tag}).status_code == 412
    assert client.patch(url, json={"title": "Renamed"}).status_code == 200
    assert client.get(url + "/content").headers["etag"] == manifest.headers["etag"]
    assert client.delete(url + "/content", headers={"If-Match": manifest.headers["etag"]}).status_code == 204
    assert client.delete(url + "/content", headers={"If-Match": manifest.headers["etag"]}).status_code == 412
    assert client.get(f"/api/v1/files/{cover}").status_code == 200
    for file_id in media:
        assert client.get(f"/api/v1/files/{file_id}").status_code == 404
    with Session(test_engine) as db:
        assert db.scalar(select(func.count()).select_from(BookSection)) == 0
        assert db.scalar(select(func.count()).select_from(BookSegment)) == 0
        assert set(db.scalars(select(PendingFileDeletion.storage_key))) == set(keys)
    assert all(file_storage.exists(key) for key in keys)
    assert client.get(url).json()["title"] == "Renamed"
    fresh = copy.deepcopy(data)
    fresh["source_pdf_file_id"] = upload(client, "source_pdf", pdf_bytes())
    for section in fresh["sections"]:
        section["audio_file_id"] = upload(client, "audio_chapter", mp3_bytes())
    result2 = client.post(url + "/content-imports", json=fresh)
    assert result2.status_code == 200, result2.text
    assert result2.json()["content_version"] != result.json()["content_version"]
    assert client.delete(url + "/content", headers={"If-Match": manifest.headers["etag"]}).status_code == 412
    assert client.delete(url).status_code == 204
    assert client.get(f"/api/v1/files/{cover}").status_code == 404


def test_pdf_only_links_are_editable_and_change_purge_etag(client):
    book, data, media, _ = setup_book(client)
    url = f"/api/v1/books/{book}"
    empty = client.get(url + "/content").headers["etag"]
    assert client.delete(url + "/content", headers={"If-Match": empty}).status_code == 204
    assert client.patch(url, json={"source_pdf_file_id": media[0]}).status_code == 200
    assert client.delete(url + "/content", headers={"If-Match": empty}).status_code == 412
    first_tag = client.get(url + "/content").headers["etag"]
    replacement = upload(client, "source_pdf", pdf_bytes())
    assert client.patch(url, json={"source_pdf_file_id": replacement}).status_code == 200
    replacement_tag = client.get(url + "/content").headers["etag"]
    assert replacement_tag != first_tag
    assert client.get(f"/api/v1/files/{media[0]}").status_code == 404
    assert client.delete(url + "/content", headers={"If-Match": first_tag}).status_code == 412
    assert client.patch(url, json={"source_pdf_file_id": None}).status_code == 200
    assert client.get(url + "/content").json()["state"] == "empty"
    assert client.get(f"/api/v1/files/{replacement}").status_code == 404
    assert client.delete(url + "/content", headers={"If-Match": replacement_tag}).status_code == 412
    assert client.get(url + "/content").headers["etag"] == empty
    assert client.get(url).json()["title"] == "Import"


def test_imported_content_protects_its_pdf_link(client):
    book, data, media, _ = setup_book(client)
    url = f"/api/v1/books/{book}"
    assert client.post(url + "/content-imports", json=data).status_code == 200
    manifest = client.get(url + "/content")
    for value in (None, upload(client, "source_pdf", pdf_bytes())):
        response = client.patch(url, json={"source_pdf_file_id": value, "title": "Blocked"})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "content_purge_required"
        assert client.get(url).json()["source_pdf_file_id"] == media[0]
        assert client.get(url).json()["title"] == "Import"
        assert client.get(url + "/content").headers["etag"] == manifest.headers["etag"]
    assert client.patch(url, json={"source_pdf_file_id": media[0]}).status_code == 200


@pytest.mark.parametrize(
    "case,code",
    [
        ("mismatch", "pdf_mismatch"),
        ("missing", "asset_not_found"),
        ("kind", "asset_kind"),
        ("page", "page_bounds"),
        ("duration", "audio_duration"),
        ("nested_only", "root_audio_required"),
        ("duplicate", "duplicate_audio"),
    ],
)
def test_invalid_asset_imports_leave_no_claims(client, test_engine, case, code):
    book, data, media, cover = setup_book(client, attach_pdf=case == "mismatch")
    if case == "mismatch":
        data["source_pdf_file_id"] = None
    if case == "missing":
        data["sections"][0]["audio_file_id"] = 9223372036854775807
    if case == "kind":
        data["source_pdf_file_id"] = cover
    if case == "page":
        data["segments"][0]["regions"][0]["page_index"] = 999
    if case == "duration":
        data["segments"][0]["narration_end_ms"] = 10000
    if case == "nested_only":
        data["sections"][1]["audio_file_id"] = None
    if case == "duplicate":
        data["sections"][0]["audio_file_id"] = media[1]
    response = client.post(f"/api/v1/books/{book}/content-imports", json=data)
    assert response.status_code == 422, response.text
    assert code in {issue["code"] for issue in response.json()["issues"]}
    with Session(test_engine) as db:
        assert db.scalar(select(func.count()).select_from(BookSegment)) == 0
        assert db.get(StoredFile, media[1]).section_id is None
        assert db.get(Book, book).content_version is None


def test_atomic_import_and_purge_failure_rollback(client, test_engine, monkeypatch):
    book, data, media, _ = setup_book(client)
    validated = validate_content(data)
    original = service.files.claim_section_audio

    def fail_after_claim(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected")

    with monkeypatch.context() as patch:
        patch.setattr(service.files, "claim_section_audio", fail_after_claim)
        with Session(test_engine) as db, pytest.raises(RuntimeError):
            service.publish(db, book, validated, dry_run=False)
    with Session(test_engine) as db:
        assert db.scalar(select(func.count()).select_from(BookSection)) == 0
        assert db.get(StoredFile, media[2]).section_id is None
    assert client.post(f"/api/v1/books/{book}/content-imports", json=data).status_code == 200
    tag = client.get(f"/api/v1/books/{book}/content").headers["etag"]
    original_delete = service.files.queue_file_deletion

    def fail_after_queue(*args, **kwargs):
        original_delete(*args, **kwargs)
        raise RuntimeError("injected")

    monkeypatch.setattr(service.files, "queue_file_deletion", fail_after_queue)
    with Session(test_engine) as db, pytest.raises(RuntimeError):
        service.purge(db, book, tag)
    assert client.get(f"/api/v1/books/{book}/content").headers["etag"] == tag
    with Session(test_engine) as db:
        assert db.scalar(select(func.count()).select_from(BookSegment)) == 2
        assert db.scalar(select(func.count()).select_from(PendingFileDeletion)) == 0
        assert db.get(StoredFile, media[1]).section_id is not None


def test_concurrent_imports_publish_exactly_once(client, test_engine):
    book, data, _, _ = setup_book(client)
    barrier = Barrier(2)

    def publish_once():
        validated = validate_content(data)
        with Session(test_engine) as db:
            barrier.wait(timeout=5)
            try:
                service.publish(db, book, validated, dry_run=False)
                return "published"
            except service.ContentExists:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: publish_once(), range(2))) == ["conflict", "published"]
    with Session(test_engine) as db:
        assert db.scalar(select(func.count()).select_from(BookSegment)) == 2


def test_concurrent_books_cannot_claim_the_same_media(client, test_engine):
    book, data, media, _ = setup_book(client)
    other = client.post("/api/v1/books", json={"title": "Other", "language": "en"}).json()["id"]
    barrier = Barrier(2)

    def attempt(book_id):
        from voice_archive_api.logic.content_validation.validation import ContentValidationError

        with Session(test_engine) as db:
            barrier.wait(timeout=5)
            try:
                service.publish(db, book_id, validate_content(data), dry_run=False)
                return book_id
            except ContentValidationError as exc:
                assert "asset_owned" in {i.code for i in exc.issues}
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [book, other]))
    assert sum(r is not None for r in results) == 1
    with Session(test_engine) as db:
        assert db.scalar(select(func.count()).select_from(BookSection)) == 2
        assert db.scalar(select(func.count()).select_from(BookSegment)) == 2
        assert db.get(StoredFile, media[0]).book_id in results


def test_dry_run_reserves_no_media_and_missing_books_are_404(client):
    book, data, _, _ = setup_book(client)
    other = client.post("/api/v1/books", json={"title": "Other", "language": "en"}).json()["id"]
    assert client.post(f"/api/v1/books/{book}/content-imports?dry_run=true", json=data).status_code == 200
    assert client.post(f"/api/v1/books/{other}/content-imports", json=data).status_code == 200
    response = client.post(f"/api/v1/books/{book}/content-imports", json=data)
    assert response.status_code == 422
    assert "asset_owned" in {issue["code"] for issue in response.json()["issues"]}
    missing = "/api/v1/books/9223372036854775807"
    assert client.post(missing + "/content-imports", json=data).status_code == 404
    assert client.get(missing + "/content").status_code == 404
    assert client.delete(missing + "/content", headers={"If-Match": '"tag"'}).status_code == 404


def test_strict_json_and_payload_bounds_before_database(schema_only_client, monkeypatch):
    from voice_archive_api.api.v1 import content

    monkeypatch.setattr(content, "MAX_PAYLOAD_BYTES", 100)
    for raw, status in [(b"{" * 101, 413), (b'{"schema_version":1,"schema_version":1}', 422), (b"not json", 422)]:
        response = schema_only_client.post(
            "/api/v1/books/1/content-imports", content=raw, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == status, response.text


def test_text_only_and_multiple_batches(client, test_engine, monkeypatch):
    monkeypatch.setattr(service, "BATCH_SIZE", 2)
    book = client.post("/api/v1/books", json={"title": "Text", "language": "en"}).json()["id"]
    sections = [
        {
            "source_key": str(i),
            "parent_source_key": str(i - 1) if i else None,
            "kind": "section",
            "title": str(i),
            "sibling_sequence": 0,
        }
        for i in range(7)
    ]
    segments = [
        {"source_key": str(i), "section_source_key": "6", "sequence": i, "kind": "prose", "html_content": "<p>Text</p>"}
        for i in range(5)
    ]
    response = client.post(
        f"/api/v1/books/{book}/content-imports",
        json={"schema_version": 1, "sections": sections[::-1], "segments": segments},
    )
    assert response.status_code == 200, response.text
    assert response.json()["section_count"] == 7
    assert (
        client.patch(
            f"/api/v1/books/{book}", json={"source_pdf_file_id": upload(client, "source_pdf", pdf_bytes())}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/books/9223372036854775807/content-imports",
            json={"schema_version": 1, "sections": [], "segments": segments},
        ).status_code
        == 422
    )


def test_audio_upload_delivery_and_limits(client, application, file_storage):
    raw = mp3_bytes()
    audio = upload(client, "audio_chapter", raw)
    full = client.get(f"/api/v1/files/{audio}")
    assert full.content == raw
    assert full.headers["content-type"] == "audio/mpeg"
    head = client.head(f"/api/v1/files/{audio}")
    assert head.content == b"" and int(head.headers["content-length"]) == len(raw)
    ranged = client.get(f"/api/v1/files/{audio}", headers={"Range": "bytes=10-99"})
    assert ranged.status_code == 206 and ranged.content == raw[10:100]
    assert client.delete(f"/api/v1/files/{audio}").status_code == 204
    for invalid in (b"", b"fake.mp3", pdf_bytes(), bytes.fromhex("fffb9000") + bytes(30)):
        response = client.post("/api/v1/files/audio_chapter", files={"file": ("test.mp3", invalid)})
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "invalid_audio"
    application.state.settings.audio_max_bytes = len(raw) - 1
    assert client.post("/api/v1/files/audio_chapter", files={"file": ("a", raw)}).status_code == 413
