"""Public read contracts, offset pagination, compression, and purge races."""

import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from tests.http.test_content import setup_book
from voice_archive_api.db.models.content import BookSection
from voice_archive_api.services.content.reads import lock_book


def publish(client):
    book, payload, media, cover = setup_book(client)
    payload["sections"].append({"source_key": "empty", "kind": "part", "title": "Empty", "sibling_sequence": 1})
    response = client.post(f"/api/v1/books/{book}/content-imports", json=payload)
    assert response.status_code == 200, response.text
    return book, payload, media, response.json()["content_version"]


def collect(client, book, version, resource, limit=1):
    items = []
    offset = 0
    while True:
        response = client.get(
            f"/api/v1/books/{book}/{resource}",
            headers={"If-Content-Version": version},
            params={
                "limit": limit,
                "offset": offset,
            },
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["content_version"] == version
        items.extend(page["items"])
        offset = page["next_offset"]
        if offset is None:
            return items


def test_complete_reads_hierarchy_targets_regions_timing_and_files(client):
    book, payload, media, version = publish(client)
    manifest = client.get(f"/api/v1/books/{book}/content").json()
    assert manifest["section_count"] == 3 and manifest["segment_count"] == 2
    assert {file["id"] for file in manifest["files"]} == set(media)
    assert "storage_key" not in str(manifest)
    sections = collect(client, book, version, "sections")
    assert [s["source_key"] for s in sections] == ["root", "child", "empty"]
    assert [s["sequence"] for s in sections] == [0, 1, 2]
    assert [s["first_segment_source_key"] for s in sections] == ["a", "a", None]
    assert sections[1]["parent_id"] == sections[0]["id"]
    assert sections[0]["audio_file_id"] == media[1]
    assert sections[1]["audio_file_id"] == media[2]
    segments = collect(client, book, version, "segments")
    assert [s["sequence"] for s in segments] == [0, 2]
    assert segments[0]["text_content"] == "Hello\nworld"
    assert segments[0]["html_content"] == payload["segments"][0]["html_content"]
    assert segments[0]["regions"] == payload["segments"][0]["regions"]
    assert segments[0]["page_indexes"] == [0]
    assert segments[0]["narration_seek_ms"] == 400
    assert segments[1]["narration_start_ms"] == 100


def test_section_offsets_count_rows_even_when_sequences_have_gaps(client, test_engine):
    book, _, _, version = publish(client)
    with Session(test_engine) as db:
        db.execute(update(BookSection).where(BookSection.book_id == book).values(sequence=BookSection.sequence * 10))
        db.commit()
    assert [row["sequence"] for row in collect(client, book, version, "sections")] == [0, 10, 20]


def test_import_assigns_preorder_independent_of_input_order_and_sibling_gaps(client):
    book = client.post("/api/v1/books", json={"title": "Preorder", "language": "en"}).json()["id"]
    sections = [
        {"source_key": key, "parent_source_key": parent, "sibling_sequence": sibling, "kind": "section", "title": key}
        for key, parent, sibling in [
            ("right", "root", 30),
            ("later", None, 20),
            ("deep", "left", 9),
            ("left", "root", 5),
            ("root", None, 10),
        ]
    ]
    result = client.post(
        f"/api/v1/books/{book}/content-imports",
        json={
            "schema_version": 1,
            "sections": sections,
            "segments": [{"source_key": "text", "sequence": 100, "kind": "prose", "html_content": "<p>Text</p>"}],
        },
    )
    assert result.status_code == 200, result.text
    rows = collect(client, book, result.json()["content_version"], "sections", limit=2)
    assert [row["source_key"] for row in rows] == ["root", "left", "deep", "right", "later"]
    assert [row["sequence"] for row in rows] == list(range(5))


@pytest.mark.parametrize("resource", ["sections", "segments"])
def test_version_and_offset_rejected_after_purge_and_fresh_import(client, resource):
    book, payload, media, version = publish(client)
    url = f"/api/v1/books/{book}"
    page = client.get(url + f"/{resource}", headers={"If-Content-Version": version}, params={"limit": 1}).json()
    offset = page["next_offset"]
    assert offset
    tag = client.get(url + "/content").headers["etag"]
    assert client.delete(url + "/content", headers={"If-Match": tag}).status_code == 204
    for fresh in (False, True):
        if fresh:
            text_only = copy.deepcopy(payload)
            text_only["source_pdf_file_id"] = None
            for section in text_only["sections"]:
                section.pop("audio_file_id", None)
            for segment in text_only["segments"]:
                segment["regions"] = []
                for key in ("narration_start_ms", "narration_end_ms", "narration_seek_ms"):
                    segment.pop(key, None)
            assert client.post(url + "/content-imports", json=text_only).status_code == 200
        response = client.get(url + f"/{resource}", headers={"If-Content-Version": version}, params={"offset": offset})
        assert response.status_code == 412
        assert response.json()["error"]["code"] == "content_version_mismatch"


@pytest.mark.parametrize("resource", ["sections", "segments"])
def test_offset_and_version_header_validation(client, resource):
    book, _, _, version = publish(client)
    url = f"/api/v1/books/{book}/{resource}"
    assert client.get(url).status_code == 422
    assert client.get(url, headers={"If-Content-Version": "invalid"}).status_code == 422
    assert client.get(url, headers={"If-Content-Version": version}, params={"offset": -1}).status_code == 422
    assert client.get(url, headers={"If-Content-Version": version}, params={"offset": 2147483648}).status_code == 422
    assert client.get(url, headers={"If-Content-Version": version}, params={"offset": 100}).json()["items"] == []
    assert client.get(url, headers={"If-Content-Version": str(uuid4())}).status_code == 412
    # Only the content collections require this header.
    assert client.get(f"/api/v1/books/{book}/content").status_code == 200
    assert client.get(f"/api/v1/books/{book}").status_code == 200
    assert client.get("/api/v1/books/999999/segments", headers={"If-Content-Version": version}).status_code == 404


def test_gzip_structured_responses_and_unchanged_media_ranges(client):
    book, _, media, version = publish(client)
    url = f"/api/v1/books/{book}"
    response = client.get(url + "/segments", headers={"If-Content-Version": version, "Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in response.headers["vary"]
    assert response.json()["items"][0]["source_key"] == "a"
    identity = client.get(url + "/segments", headers={"If-Content-Version": version, "Accept-Encoding": "identity"})
    assert "content-encoding" not in identity.headers
    assert int(response.headers["content-length"]) < len(identity.content)
    for file_id in media:
        file_url = f"/api/v1/files/{file_id}"
        full = client.get(file_url, headers={"Accept-Encoding": "gzip"})
        assert "content-encoding" not in full.headers
        partial = client.get(file_url, headers={"Range": "bytes=0-15", "Accept-Encoding": "gzip"})
        assert partial.status_code == 206 and partial.content == full.content[:16]
        assert "content-encoding" not in partial.headers
        assert (
            client.get(file_url, headers={"Range": "bytes=0-15", "If-Range": full.headers["etag"]}).status_code == 206
        )
        assert client.get(file_url, headers={"Range": "bytes=0-15", "If-Range": '"stale"'}).status_code == 200


def test_shared_read_lock_blocks_purge_until_response_snapshot_released(client, test_engine):
    book, _, _, version = publish(client)
    tag = client.get(f"/api/v1/books/{book}/content").headers["etag"]
    entered = Event()
    with Session(test_engine) as db, ThreadPoolExecutor(max_workers=1) as pool:
        lock_book(db, book)

        def purge():
            entered.set()
            return client.delete(f"/api/v1/books/{book}/content", headers={"If-Match": tag})

        pending = pool.submit(purge)
        assert entered.wait(5)
        assert not pending.done()
        db.rollback()
        assert pending.result(timeout=10).status_code == 204
