"""Discover the executable upload and delivery specifications under book_files."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from httpx import Response
from PIL import Image
from pytest_bdd import given, parsers, scenarios, then, when
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.file_processing.file_samples import cover_bytes, pdf_bytes, pdf_bytes_of_size
from voice_archive_api.book_files.model import PendingFileDeletion, StoredFile

scenarios("../features/book_files")


@dataclass
class UploadScenario:
    path: str = ""
    filename: str = "upload.bin"
    media_type: str = "application/octet-stream"
    content: bytes = b""
    response: Response | None = None


@pytest.fixture
def upload():
    return UploadScenario()


@dataclass
class DeliveryScenario:
    kind: str = "cover"
    url: str = ""
    content: bytes = b""
    response: Response | None = None


@pytest.fixture
def delivery():
    return DeliveryScenario()


def file_api_path(kind):
    return {"cover": "/api/v1/files/cover", "source PDF": "/api/v1/files/source_pdf"}[kind]


FILE_RESPONSE_FIELDS = {"id", "kind", "byte_size", "metadata", "created_at"}


@given("I am uploading a cover")
def cover_upload(upload):
    upload.path = file_api_path("cover")


@given("I am uploading a source PDF")
def pdf_upload(upload):
    upload.path = file_api_path("source PDF")


@given(parsers.parse("I have selected a {format} image"))
def select_image(upload, format):
    upload.content = cover_bytes(format)


@given(parsers.parse("I have selected a PDF with {page_count:d} pages"))
def select_pdf(upload, page_count):
    upload.content = pdf_bytes(page_count)


@given("I have selected a scanned PDF")
def select_scanned_pdf(upload):
    upload.content = pdf_bytes(scanned=True)


@given(parsers.re(r'I have selected an encrypted PDF with password "(?P<password>.*)"'))
def select_encrypted_pdf(upload, password):
    upload.content = pdf_bytes(password=password)


@given(parsers.parse("I have selected a file with {sample} content"))
def select_invalid_bytes(upload, sample):
    upload.content = {"empty": b"", "corrupt": b"not a valid file"}[sample]


@given(parsers.parse('the filename is "{filename}" and the declared MIME type is "{media_type}"'))
def misleading_name_and_type(upload, filename, media_type):
    upload.filename = filename
    upload.media_type = media_type


def set_file_limit(application, upload, limit):
    settings = application.state.settings
    key = {file_api_path("cover"): "cover_max_bytes", file_api_path("source PDF"): "pdf_max_bytes"}[upload.path]
    setattr(settings, key, limit)


@given("the file size equals the configured limit")
def exact_limit(application, upload):
    set_file_limit(application, upload, len(upload.content))


@given("the file size is one byte above the configured limit")
def over_limit(application, upload):
    set_file_limit(application, upload, len(upload.content) - 1)


def stored_files(storage):
    return {path: path.read_bytes() for path in storage.root.rglob("*") if path.is_file()}


@when("I upload the file")
def upload_file(client, upload):
    upload.response = client.post(
        upload.path,
        files={
            "file": (upload.filename, upload.content, upload.media_type),
        },
    )


def result(state: UploadScenario | DeliveryScenario) -> Response:
    assert state.response is not None
    return state.response


def assert_ready_response(upload, expected_fields):
    response = result(upload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == expected_fields
    assert body["id"] > 0
    assert datetime.fromisoformat(body["created_at"]).utcoffset() == timedelta(0)
    return body


@then("the cover is ready to link to a book")
def ready_cover(test_engine, file_storage, upload):
    body = assert_ready_response(upload, FILE_RESPONSE_FIELDS)
    with Session(test_engine) as db:
        record = db.get(StoredFile, body["id"])
        assert record is not None
        key = record.storage_key
        assert isinstance(key, str)
        with file_storage.open(key) as stream, Image.open(stream) as image:
            assert image.format == "WEBP"
            assert image.size == (body["metadata"]["width"], body["metadata"]["height"]) == (16, 24)
        assert (file_storage.root / key).stat().st_size == body["byte_size"]
        assert list(stored_files(file_storage)) == [file_storage.root / key]


@then("the PDF is ready to link to a book")
def ready_pdf(test_engine, file_storage, upload):
    body = assert_ready_response(upload, FILE_RESPONSE_FIELDS)
    with Session(test_engine) as db:
        record = db.get(StoredFile, body["id"])
        assert record is not None
        key = record.storage_key
        assert isinstance(key, str)
        with file_storage.open(key) as stream:
            assert stream.read() == upload.content
        assert body["byte_size"] == len(upload.content)
        assert list(stored_files(file_storage)) == [file_storage.root / key]


@then(parsers.parse("the PDF has {page_count:d} pages"))
def pdf_page_count(upload, page_count):
    assert result(upload).json()["metadata"]["page_count"] == page_count


@then("no book has been created")
def no_book(client):
    assert client.get("/api/v1/books").json()["total"] == 0


@then(parsers.parse('the upload fails with status {status:d} and code "{code}"'))
def failed_upload(upload, status, code):
    response = result(upload)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


@then("no file has been prepared")
def no_file(test_engine, file_storage):
    with Session(test_engine) as db:
        assert db.scalar(select(func.count()).select_from(StoredFile)) == 0
    assert stored_files(file_storage) == {}


@given(parsers.re(r"I have uploaded a prepared (?P<kind>cover|source PDF)"))
def prepare_file(client, file_storage, upload, delivery, kind):
    upload.path = file_api_path(kind)
    upload.content = {"cover": cover_bytes, "source PDF": pdf_bytes}[kind]()
    prepare_delivery(client, file_storage, upload, delivery)


@given(parsers.parse("I have uploaded a prepared source PDF of {byte_size:d} bytes"))
def prepare_sized_pdf(client, file_storage, upload, delivery, byte_size):
    upload.path = file_api_path("source PDF")
    upload.content = pdf_bytes_of_size(byte_size)
    prepare_delivery(client, file_storage, upload, delivery)
    assert len(delivery.content) == byte_size


def prepare_delivery(client, file_storage, upload, delivery):
    upload_file(client, upload)
    body = assert_ready_response(upload, FILE_RESPONSE_FIELDS)
    delivery.kind = body["kind"]
    delivery.url = f"/api/v1/files/{body['id']}"

    # Keep actual stored bytes separate from upload input: normalization can
    # change a cover, and delivery must match the stored representation.
    stored_contents = list(stored_files(file_storage).values())
    assert len(stored_contents) == 1
    delivery.content = stored_contents[0]
    assert len(delivery.content) == body["byte_size"]


@given(parsers.parse("the prepared {kind} is missing"))
def point_to_missing_file(delivery, kind):
    delivery.url = "/api/v1/files/999999"


@given("the stored file has been removed")
def remove_stored_file(file_storage):
    for path in file_storage.root.rglob("*"):
        if path.is_file():
            path.unlink()


@when("I retrieve the prepared file")
def retrieve_file(client, delivery):
    delivery.response = client.get(delivery.url)


@when("I request the prepared file headers")
def inspect_file(client, delivery):
    delivery.response = client.head(delivery.url)


@when(parsers.parse("I request bytes from {start} through {end}"))
def retrieve_range(client, delivery, start, end):
    size = len(delivery.content)

    def absolute_position(value):
        if value == "last":
            return size - 1
        position = int(value)
        return size + position if position < 0 else position

    if start.startswith("-") and end == "last":
        # A suffix requests the final N bytes without specifying an absolute end.
        range_header = f"bytes={start}"
    else:
        first = absolute_position(start)
        last = "" if end == "last" else str(absolute_position(end))
        range_header = f"bytes={first}-{last}"
    delivery.response = client.get(delivery.url, headers={"Range": range_header})


@when("I request a range starting beyond the prepared file")
def retrieve_range_beyond_file(client, delivery):
    start = len(delivery.content) + 1
    delivery.response = client.get(delivery.url, headers={"Range": f"bytes={start}-"})


@then("the complete prepared file is returned")
def complete_file(delivery):
    response = result(delivery)
    assert response.status_code == 200, response.text
    assert response.content == delivery.content
    assert int(response.headers["content-length"]) == len(delivery.content)


@then(parsers.parse("delivery headers identify the {kind}"))
def representation_headers(delivery, kind):
    headers = result(delivery).headers
    media_type, filename = {"cover": ("image/webp", "cover.webp"), "source PDF": ("application/pdf", "source.pdf")}[
        kind
    ]
    assert headers["content-type"] == media_type
    assert headers["content-disposition"] == f'inline; filename="{filename}"'
    assert headers["accept-ranges"] == "bytes"
    assert headers["etag"]
    assert headers["last-modified"]


@then("the headers describe the complete file without a body")
def headers_without_body(client, delivery):
    response = result(delivery)
    assert response.status_code == 200
    assert response.content == b""
    get_headers = client.get(delivery.url).headers
    for name in ("content-type", "content-length", "content-disposition", "accept-ranges", "etag", "last-modified"):
        assert response.headers[name] == get_headers[name]


@then(parsers.parse("only stored bytes {start:d} through {end:d} are returned"))
def selected_bytes(delivery, start, end):
    response = result(delivery)
    size = len(delivery.content)
    assert 0 <= start <= end < size
    assert response.status_code == 206, response.text
    assert response.content == delivery.content[start : end + 1]
    assert response.headers["content-range"] == f"bytes {start}-{end}/{size}"
    assert int(response.headers["content-length"]) == end - start + 1
    assert response.headers["content-type"] == "application/pdf"


@then("I am told that the requested range is outside the file")
def range_outside_file(delivery):
    response = result(delivery)
    assert response.status_code == 416, response.text
    assert response.content == b""


@then("I am told that the requested range is invalid")
def invalid_range(delivery):
    response = result(delivery)
    assert response.status_code == 400, response.text
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text


@then(parsers.parse("I am told that the {kind} cannot be found"))
def file_not_found(delivery, kind):
    response = result(delivery)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "file_not_found"


@then("the range error identifies the complete file size")
def unsatisfiable_size(delivery):
    assert result(delivery).headers["content-range"] == f"bytes */{len(delivery.content)}"


def prepared_file_identity(delivery):
    file_id = int(delivery.url.rsplit("/", 1)[1])
    return StoredFile, file_id, {"cover": "cover_file_id", "source_pdf": "source_pdf_file_id"}[delivery.kind]


@given("the prepared file is linked to a book")
def link_prepared_file(client, delivery):
    _, file_id, field = prepared_file_identity(delivery)
    response = client.post(
        "/api/v1/books",
        json={
            "title": "File owner",
            "language": "en",
            field: file_id,
        },
    )
    assert response.status_code == 201, response.text


@given("I have deleted the prepared file")
def already_deleted_file(client, delivery):
    response = client.delete(delivery.url)
    assert response.status_code == 204, response.text


@when("I delete the prepared file")
def delete_prepared_file(client, delivery):
    delivery.response = client.delete(delivery.url)


@then("the prepared file is removed and its bytes are queued for deletion")
def prepared_file_deleted(client, test_engine, delivery):
    response = result(delivery)
    assert response.status_code == 204, response.text
    assert response.content == b""
    model, file_id, _ = prepared_file_identity(delivery)
    with Session(test_engine) as db:
        assert db.get(model, file_id) is None
        assert db.scalars(select(PendingFileDeletion)).one() is not None
    assert client.get(delivery.url).status_code == 404


@then("the stored bytes remain until maintenance runs")
def deletion_bytes_preserved(test_engine, file_storage, delivery):
    with Session(test_engine) as db:
        pending = db.scalars(select(PendingFileDeletion)).one()
        with file_storage.open(pending.storage_key) as stream:
            assert stream.read() == delivery.content


@then("I am told that the prepared file is in use by a book")
def prepared_file_in_use(delivery):
    response = result(delivery)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "file_already_linked"


@then("the prepared file and its book link remain available")
def linked_file_preserved(client, test_engine, delivery):
    model, file_id, field = prepared_file_identity(delivery)
    book = client.get("/api/v1/books").json()["items"][0]
    assert book[field] == file_id
    response = client.get(delivery.url)
    assert response.status_code == 200, response.text
    assert response.content == delivery.content
    with Session(test_engine) as db:
        assert db.get(model, file_id) is not None
        assert db.scalar(select(func.count()).select_from(PendingFileDeletion)) == 0
