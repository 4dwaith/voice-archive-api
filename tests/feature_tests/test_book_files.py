"""Discover the executable upload specifications under book_files."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from httpx import Response
from PIL import Image
from pytest_bdd import given, parsers, scenarios, then, when
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.storage.file_samples import cover_bytes, pdf_bytes
from voice_archive_api.book_files.model import CoverFile, SourcePdfFile

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


@given("I am uploading a cover")
def cover_upload(upload):
    upload.path = "/api/v1/cover-files"


@given("I am uploading a source PDF")
def pdf_upload(upload):
    upload.path = "/api/v1/source-pdf-files"


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
    if upload.path == "/api/v1/cover-files":
        settings.cover_max_bytes = limit
    else:
        settings.pdf_max_bytes = limit


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
    upload.response = client.post(upload.path, files={
        "file": (upload.filename, upload.content, upload.media_type),
    })


def result(upload):
    assert upload.response is not None
    return upload.response


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
    body = assert_ready_response(upload, {"id", "byte_size", "width", "height", "created_at"})
    with Session(test_engine) as db:
        record = db.get(CoverFile, body["id"])
        assert record is not None
        key = record.storage_key
        assert isinstance(key, str)
        with file_storage.open(key) as stream, Image.open(stream) as image:
            assert image.format == "WEBP"
            assert image.size == (body["width"], body["height"]) == (16, 24)
        assert (file_storage.root / key).stat().st_size == body["byte_size"]
        assert list(stored_files(file_storage)) == [file_storage.root / key]


@then("the PDF is ready to link to a book")
def ready_pdf(test_engine, file_storage, upload):
    body = assert_ready_response(upload, {"id", "byte_size", "page_count", "created_at"})
    with Session(test_engine) as db:
        record = db.get(SourcePdfFile, body["id"])
        assert record is not None
        key = record.storage_key
        assert isinstance(key, str)
        with file_storage.open(key) as stream:
            assert stream.read() == upload.content
        assert body["byte_size"] == len(upload.content)
        assert list(stored_files(file_storage)) == [file_storage.root / key]


@then(parsers.parse("the PDF has {page_count:d} pages"))
def pdf_page_count(upload, page_count):
    assert result(upload).json()["page_count"] == page_count


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
        assert db.scalar(select(func.count()).select_from(CoverFile)) == 0
        assert db.scalar(select(func.count()).select_from(SourcePdfFile)) == 0
    assert stored_files(file_storage) == {}


