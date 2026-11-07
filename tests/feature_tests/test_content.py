"""Executable content publication/purge specifications through real HTTP and PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Barrier
from uuid import UUID

import pytest
from httpx import Response
from pytest_bdd import given, parsers, scenarios, then, when

from tests.file_processing.file_samples import cover_bytes, mp3_bytes, pdf_bytes

scenarios("../features/content")


@dataclass
class ContentScenario:
    book_id: int = 0
    original_book: dict = field(default_factory=dict)
    payload: dict = field(default_factory=dict)
    media: dict[str, int] = field(default_factory=dict)
    audio_bytes: bytes = b""
    empty_tag: str = ""
    old_tag: str = ""
    old_version: str = ""
    before: dict = field(default_factory=dict)
    removed_files: list[int] = field(default_factory=list)
    response: Response | None = None
    concurrent_responses: list[Response] = field(default_factory=list)

    @property
    def url(self):
        return f"/api/v1/books/{self.book_id}"


@pytest.fixture
def content():
    return ContentScenario()


def result(content):
    assert content.response is not None, "No action has been performed"
    return content.response


def upload(client, kind, raw):
    response = client.post(f"/api/v1/files/{kind}", files={"file": (f"sample-{kind}", raw)})
    assert response.status_code == 201, response.text
    return response.json()


def prepare_media(client, content):
    content.audio_bytes = mp3_bytes()
    pdf = upload(client, "source_pdf", pdf_bytes(page_count=2))
    root = upload(client, "audio_chapter", content.audio_bytes)
    nested = upload(client, "audio_chapter", mp3_bytes(frame_count=8))
    # Nested text ends after this shorter nested-owned file, but within root audio.
    # Successful publication therefore exercises root-only narration resolution.
    assert nested["metadata"]["duration_ms"] < 500 < 900 <= root["metadata"]["duration_ms"]
    content.media = {"pdf": pdf["id"], "root": root["id"], "nested": nested["id"]}


def fetch_manifest(client, content):
    response = client.get(content.url + "/content")
    assert response.status_code == 200, response.text
    return response


def read_collection(client, content, resource):
    current = fetch_manifest(client, content).json()

    if current["state"] != "published":
        return []

    items = []
    offset = 0
    while True:
        response = client.get(
            content.url + "/" + resource,
            headers={"If-Content-Version": current["content_version"]},
            params={"offset": offset},
        )
        assert response.status_code == 200, response.text
        items.extend(response.json()["items"])
        offset = response.json()["next_offset"]
        if offset is None:
            return items


def snapshot(client, test_engine, content):
    book = client.get(content.url)
    assert book.status_code == 200, book.text
    current = fetch_manifest(client, content)
    files = {}
    for file_id in content.media.values():
        response = client.head(f"/api/v1/files/{file_id}")
        files[file_id] = (response.status_code, response.headers.get("etag"), response.headers.get("content-length"))
    return {
        "book": book.json(),
        "manifest": current.json(),
        "etag": current.headers["etag"],
        "sections": read_collection(client, content, "sections"),
        "segments": read_collection(client, content, "segments"),
        "files": files,
    }


def import_request(client, test_engine, content, *, dry_run=False, book_id=None):
    content.before = snapshot(client, test_engine, content)
    url = content.url if book_id is None else f"/api/v1/books/{book_id}"
    content.response = client.post(url + "/content-imports", params={"dry_run": dry_run}, json=content.payload)


def purge_request(client, test_engine, content, tag):
    content.before = snapshot(client, test_engine, content)
    content.removed_files = [file["id"] for file in fetch_manifest(client, content).json()["files"]]
    content.response = client.delete(content.url + "/content", headers={} if tag is None else {"If-Match": tag})


@given("a book and valid prepared text, PDF, and chapter audio")
def prepared_book(client, content):
    cover = upload(client, "cover", cover_bytes())
    response = client.post(
        "/api/v1/books",
        json={
            "title": "Prepared book",
            "author": "Example author",
            "description": "Keep these details",
            "language": "en",
            "cover_file_id": cover["id"],
        },
    )
    assert response.status_code == 201, response.text
    content.original_book = response.json()
    content.book_id = content.original_book["id"]
    content.empty_tag = fetch_manifest(client, content).headers["etag"]
    prepare_media(client, content)
    content.payload = {
        "schema_version": 1,
        "source_pdf_file_id": content.media["pdf"],
        "sections": [
            {
                "source_key": "nested",
                "parent_source_key": "root",
                "kind": "section",
                "title": "Nested",
                "sibling_sequence": 0,
                "audio_file_id": content.media["nested"],
            },
            {
                "source_key": "root",
                "kind": "chapter",
                "title": "Chapter",
                "sibling_sequence": 0,
                "audio_file_id": content.media["root"],
            },
        ],
        "segments": [
            {
                "source_key": "heading",
                "section_source_key": "root",
                "sequence": 0,
                "kind": "heading",
                "html_content": "<h1>Chapter</h1>",
                "narration_start_ms": 0,
                "narration_end_ms": 80,
            },
            {
                "source_key": "prose",
                "section_source_key": "nested",
                "sequence": 2,
                "kind": "prose",
                "html_content": "<p><strong>Hello</strong><br>world</p>",
                "language": "en",
                "regions": [
                    {"page_index": 0, "x0": 0.1, "y0": 0.2, "x1": 0.4, "y1": 0.5},
                    {"page_index": 1, "x0": 0.2, "y0": 0.1, "x1": 0.5, "y1": 0.2},
                    {"page_index": 1, "x0": 0.2, "y0": 0.3, "x1": 0.5, "y1": 0.4},
                ],
                "narration_start_ms": 500,
                "narration_end_ms": 900,
                "narration_seek_ms": 400,
                "alignment_status": "interpolated",
                "alignment_metadata": {"coverage": 0.75},
            },
            {
                "source_key": "verse",
                "section_source_key": "root",
                "sequence": 4,
                "kind": "verse",
                "html_content": '<p class="no-indent">Earlier<br><em>line</em></p>',
                "narration_start_ms": 100,
                "narration_end_ms": 300,
            },
        ],
    }


@given("the prepared PDF is already attached to the book")
def attach_pdf(client, content):
    response = client.patch(content.url, json={"source_pdf_file_id": content.media["pdf"]})
    assert response.status_code == 200, response.text


@given("the prepared content refers to another PDF")
def another_pdf(client, content):
    content.payload["source_pdf_file_id"] = upload(client, "source_pdf", pdf_bytes())["id"]


@given("the prepared content is text-only")
def text_only(content):
    content.payload["source_pdf_file_id"] = None
    content.payload["sections"] = []
    for segment in content.payload["segments"]:
        segment.pop("section_source_key")
        segment.pop("regions", None)
        for key in ("narration_start_ms", "narration_end_ms", "narration_seek_ms"):
            segment.pop(key, None)


@given("the prepared content has already been imported")
def already_imported(client, test_engine, content):
    import_request(client, test_engine, content)
    assert result(content).status_code == 200, result(content).text


@given(parsers.parse("the prepared input has {problem}"))
def invalid_prepared_content(client, content, problem):
    if problem == "unsupported formatting":
        content.payload["segments"][1]["html_content"] = "<p><script>Invalid</script></p>"
    elif problem == "a missing section reference":
        content.payload["segments"][1]["section_source_key"] = "missing"
    elif problem == "cyclic sections":
        content.payload["sections"][1]["parent_source_key"] = "nested"
    elif problem == "a PDF region outside the book":
        content.payload["segments"][1]["regions"][0]["page_index"] = 2
    elif problem == "a missing audio file":
        content.payload["sections"][1]["audio_file_id"] = 9223372036854775807
    elif problem == "a shared audio file":
        content.payload["sections"][0]["audio_file_id"] = content.media["root"]
    elif problem == "the wrong file kind":
        content.payload["source_pdf_file_id"] = content.original_book["cover_file_id"]
    elif problem == "a PDF belonging to another book":
        response = client.post(
            "/api/v1/books",
            json={
                "title": "Other owner",
                "language": "en",
                "source_pdf_file_id": content.media["pdf"],
            },
        )
        assert response.status_code == 201, response.text
    elif problem == "narration beyond the audio":
        content.payload["segments"][1]["narration_end_ms"] = 10000
    elif problem == "nested audio without root audio":
        content.payload["sections"][1]["audio_file_id"] = None
    else:
        assert problem == "an unknown segment kind"
        content.payload["segments"][1]["kind"] = "unknown"


@given("the prepared content was purged and imported again")
def fresh_import(client, test_engine, content):
    already_imported(client, test_engine, content)
    old = fetch_manifest(client, content)
    content.old_tag = old.headers["etag"]
    content.old_version = old.json()["content_version"]
    purge_request(client, test_engine, content, content.old_tag)
    assert result(content).status_code == 204
    prepare_media(client, content)
    content.payload["source_pdf_file_id"] = content.media["pdf"]
    content.payload["sections"][0]["audio_file_id"] = content.media["nested"]
    content.payload["sections"][1]["audio_file_id"] = content.media["root"]
    already_imported(client, test_engine, content)


@when("I import the prepared content")
def import_content(client, test_engine, content):
    import_request(client, test_engine, content)


@when("I dry-run the prepared content")
def dry_run(client, test_engine, content):
    import_request(client, test_engine, content, dry_run=True)


@when("I import the prepared content into a missing book")
def missing_book(client, test_engine, content):
    import_request(client, test_engine, content, book_id=9223372036854775807)


@when("two requests import the prepared content at the same time")
def concurrent_imports(client, test_engine, content):
    content.before = snapshot(client, test_engine, content)
    barrier = Barrier(2)

    def send_request():
        barrier.wait(timeout=5)
        return client.post(content.url + "/content-imports", json=content.payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send_request) for _ in range(2)]
        content.concurrent_responses = [future.result(timeout=15) for future in futures]


@when("I purge content using its current state tag")
def purge_current(client, test_engine, content):
    purge_request(client, test_engine, content, fetch_manifest(client, content).headers["etag"])


@when("I purge content using the old import state tag")
def purge_old(client, test_engine, content):
    purge_request(client, test_engine, content, content.old_tag)


@when(parsers.parse("I purge content using a {kind} state tag"))
def purge_with_tag(client, test_engine, content, kind):
    current = fetch_manifest(client, content).headers["etag"]
    tags = {"missing": None, "stale": content.empty_tag, "weak": "W/" + current, "wildcard": "*"}
    purge_request(client, test_engine, content, tags[kind])


def import_succeeds(client, content):
    response = result(content)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["dry_run"] is False
    assert data["section_count"] == len(content.payload["sections"])
    assert data["segment_count"] == len(content.payload["segments"])
    assert UUID(data["content_version"]).version == 4
    current = fetch_manifest(client, content)
    assert current.json() == {
        "summary": None,
        "state": "published",
        "content_version": data["content_version"],
        "schema_version": 1,
        "imported_at": current.json()["imported_at"],
        "source_pdf_file_id": content.payload["source_pdf_file_id"],
        "section_count": len(content.payload["sections"]),
        "segment_count": len(content.payload["segments"]),
        "files": current.json()["files"],
    }
    assert current.json()["imported_at"] is not None
    assert current.headers["etag"].startswith('"') and not current.headers["etag"].startswith("W/")


def dry_run_succeeds(content):
    response = result(content)
    assert response.status_code == 200, response.text
    assert response.json() == {"dry_run": True, "section_count": 2, "segment_count": 3, "content_version": None}


@then("the complete ordered dataset and its formatting are stored")
def complete_dataset(client, content):
    # Every supplied section must be present, with no additional sections.
    sections = {row["source_key"]: row for row in read_collection(client, content, "sections")}
    assert set(sections) == {s["source_key"] for s in content.payload["sections"]}

    for supplied in content.payload["sections"]:
        row = sections[supplied["source_key"]]

        # Import resolves parent source keys to database IDs; roots remain parentless.
        parent = sections.get(supplied.get("parent_source_key"))
        assert row["parent_id"] == (parent["id"] if parent else None)

        # Section metadata and ordering among siblings must survive unchanged.
        for key in ("kind", "title", "sibling_sequence"):
            assert row[key] == supplied[key]

    # Read all segment pages and check completeness before comparing document order.
    rows = read_collection(client, content, "segments")
    assert len(rows) == len(content.payload["segments"])

    expected_text = {"heading": "Chapter", "prose": "Hello\nworld", "verse": "Earlier\nline"}

    for row, supplied in zip(rows, content.payload["segments"], strict=True):
        # Segment ownership uses the resolved section ID, or null for unsectioned text.
        owner = sections.get(supplied.get("section_source_key"))
        assert row["section_id"] == (owner["id"] if owner else None)

        # Comparing in response order verifies identity, sequence, kind, and retained HTML.
        for key in ("source_key", "sequence", "kind", "html_content"):
            assert row[key] == supplied[key]

        # Derived plain text must preserve the fixture's text and explicit line breaks.
        assert row["text_content"] == expected_text[row["source_key"]]

        # Regions retain their input order; generated page indexes are sorted and unique.
        assert row["regions"] == supplied.get("regions", [])
        assert row["page_indexes"] == sorted({r["page_index"] for r in supplied.get("regions", [])})

        # Optional language, narration timing, and alignment diagnostics round-trip as supplied.
        # Fields omitted from the input are exposed as null.
        for key in (
            "language",
            "narration_start_ms",
            "narration_end_ms",
            "narration_seek_ms",
            "alignment_status",
            "alignment_metadata",
        ):
            assert row[key] == supplied.get(key)


@then("the PDF and each chapter audio belong to their intended owners")
def media_ownership(client, content):
    files = {file["id"]: file for file in fetch_manifest(client, content).json()["files"]}
    pdf = files[content.media["pdf"]]
    assert pdf["book_id"] == content.book_id and pdf["section_id"] is None
    sections = {row["source_key"]: row["id"] for row in read_collection(client, content, "sections")}
    for key in ("root", "nested"):
        audio = files[content.media[key]]
        assert audio["section_id"] == sections[key] and audio["book_id"] is None


@then("chapter audio can be read through byte-range delivery")
def audio_delivery(client, content):
    url = f"/api/v1/files/{content.media['root']}"
    head = client.head(url)
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-type"] == "audio/mpeg"
    assert int(head.headers["content-length"]) == len(content.audio_bytes)
    response = client.get(url, headers={"Range": "bytes=0-15"})
    assert response.status_code == 206 and response.content == content.audio_bytes[:16]
    assert response.headers["content-range"] == f"bytes 0-15/{len(content.audio_bytes)}"


@then("the book and prepared files are unchanged")
def unchanged(client, test_engine, content):
    assert snapshot(client, test_engine, content) == content.before


@then(parsers.parse("I was told {message}"))
def outcome_message(client, content, message):
    if message == "the content was imported successfully":
        import_succeeds(client, content)
        return
    if message == "the prepared content is valid without importing it":
        dry_run_succeeds(content)
        return
    if message == "the content was purged successfully":
        purge_succeeds(content)
        return

    errors = {
        "the book already has imported content": (409, "content_exists"),
        "the book could not be found": (404, "book_not_found"),
        "the chapter audio is still in use": (409, "file_already_linked"),
        "the content state must be specified before purging": (428, "content_precondition_required"),
        "the supplied content state cannot authorize this purge": (412, "content_precondition_failed"),
    }
    validation_issues = {
        "the formatting is unsupported": "invalid_html",
        "a referenced section is missing": "missing_section",
        "the sections form a cycle": "section_cycle",
        "a PDF region falls outside the book": "page_bounds",
        "a referenced audio file could not be found": "asset_not_found",
        "the same audio file cannot belong to multiple chapters": "duplicate_audio",
        "a referenced file has the wrong kind": "asset_kind",
        "the PDF belongs to another book": "asset_owned",
        "the narration extends beyond the audio": "audio_duration",
        "nested narration requires root chapter audio": "root_audio_required",
        "the segment kind is unsupported": "enum",
        "the content refers to a different PDF than the one attached to the book": "pdf_mismatch",
    }
    response = result(content)
    if message in errors:
        status, code = errors[message]
        assert response.status_code == status, response.text
        assert response.json()["error"]["code"] == code
        return

    code = validation_issues[message]
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "content_validation_error"
    issues = response.json()["issues"]
    assert code in {issue["code"] for issue in issues}
    assert all(set(issue) == {"code", "path", "source_key", "detail"} for issue in issues)
    assert all(issue["path"].startswith("$.") and issue["detail"] for issue in issues)


@then("one import succeeds and the other reports existing content")
def one_winner(content):
    assert sorted(r.status_code for r in content.concurrent_responses) == [200, 409]
    conflict = next(r for r in content.concurrent_responses if r.status_code == 409)
    assert conflict.json()["error"]["code"] == "content_exists"


def purge_succeeds(content):
    assert result(content).status_code == 204, result(content).text
    assert result(content).content == b""


@then("the book content is empty and its owned media are unavailable")
def purged(client, test_engine, content):
    assert fetch_manifest(client, content).json() == {
        "summary": None,
        "state": "empty",
        "content_version": None,
        "schema_version": None,
        "imported_at": None,
        "source_pdf_file_id": None,
        "section_count": 0,
        "segment_count": 0,
        "files": [],
    }
    assert read_collection(client, content, "sections") == []
    assert read_collection(client, content, "segments") == []
    for file_id in content.removed_files:
        assert client.get(f"/api/v1/files/{file_id}").status_code == 404


@then("the cover and descriptive book details are preserved")
def book_preserved(client, content):
    response = client.get(content.url)
    assert response.status_code == 200
    for key in ("title", "author", "description", "language", "cover_file_id", "cover_url", "created_at"):
        assert response.json()[key] == content.original_book[key]
    assert client.get(response.json()["cover_url"]).status_code == 200


@then("the fresh import has a different content version")
def new_version(client, content):
    current = fetch_manifest(client, content)
    assert current.json()["content_version"] != content.old_version
    assert current.headers["etag"] != content.old_tag


@then("the unused prepared media remain unlinked")
@then("the unused chapter audio remain unlinked")
def unlinked_unused_media(client, content):
    ids = [content.media["root"], content.media["nested"]]
    if content.payload["source_pdf_file_id"] is None:
        ids.append(content.media["pdf"])
    owned = {file["id"] for file in fetch_manifest(client, content).json()["files"]}
    for file_id in ids:
        assert file_id not in owned
        # Public DELETE succeeds only for an unlinked prepared file.
        assert client.delete(f"/api/v1/files/{file_id}").status_code == 204


@when(parsers.parse("I delete the linked {section} chapter audio"))
def delete_linked_audio(client, test_engine, content, section):
    content.before = snapshot(client, test_engine, content)
    content.response = client.delete(f"/api/v1/files/{content.media[section]}")
