from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from pypdf import PdfWriter
from pypdf.errors import DependencyError

from voice_archive_api.domain.files.pdfs import EncryptedPdfNotSupported, InvalidPdf, prepare_pdf
from voice_archive_api.file_handling import FileTooLarge, LocalStorage


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "uploads")


def pdf_bytes(page_count=1, password=None) -> bytes:
    with PdfWriter() as writer, BytesIO() as output:
        for _ in range(page_count):
            writer.add_blank_page(width=100, height=150)
        if password is not None:
            writer.encrypt(password, algorithm="RC4-128")
        writer.write(output)
        return output.getvalue()


def remaining_files(storage) -> list[Path]:
    return [path for path in storage.root.rglob("*") if path.is_file()]


@pytest.mark.parametrize("page_count", [1, 3])
def test_preparation_counts_pages_and_preserves_original_bytes(storage, page_count):
    payload = pdf_bytes(page_count)
    source = BytesIO(payload)
    prepared = prepare_pdf(source, storage, max_bytes=len(payload))
    assert not source.closed
    assert prepared.storage_key.startswith("promoted/")
    assert prepared.byte_size == len(payload)
    assert prepared.metadata["page_count"] == page_count
    assert remaining_files(storage) == [storage.root / prepared.storage_key]
    with storage.open(prepared.storage_key) as output:
        assert output.read() == payload


def test_image_based_pdf_is_accepted(storage):
    # A small generated scan-like page avoids depending on a developer's book.
    with Image.new("RGB", (32, 48), "white") as image, BytesIO() as buffer:
        image.save(buffer, "PDF")
        payload = buffer.getvalue()
    prepared = prepare_pdf(BytesIO(payload), storage, max_bytes=len(payload))
    assert prepared.metadata["page_count"] == 1
    with storage.open(prepared.storage_key) as output:
        assert output.read() == payload


@pytest.mark.parametrize("payload", [b"", b"not a PDF", b"%PDF-1.7\ninvalid body"])
def test_invalid_input_is_not_promoted(storage, payload):
    with patch.object(storage, "promote", wraps=storage.promote) as promote:
        with pytest.raises(InvalidPdf):
            prepare_pdf(BytesIO(payload), storage, max_bytes=100_000)
        promote.assert_not_called()
    assert remaining_files(storage) == []


def test_zero_page_pdf_is_rejected(storage):
    with pytest.raises(InvalidPdf):
        prepare_pdf(BytesIO(pdf_bytes(page_count=0)), storage, max_bytes=100_000)
    assert remaining_files(storage) == []


def test_truncated_pdf_is_rejected(storage):
    payload = pdf_bytes()[:-30]
    with pytest.raises(InvalidPdf):
        prepare_pdf(BytesIO(payload), storage, max_bytes=100_000)
    assert remaining_files(storage) == []


def test_damaged_cross_reference_is_rejected_instead_of_repaired(storage):
    payload = pdf_bytes()
    beginning, marker, _ = payload.rpartition(b"startxref\n")
    assert marker
    payload = beginning + marker + b"1\n%%EOF\n"
    with pytest.raises(InvalidPdf):
        prepare_pdf(BytesIO(payload), storage, max_bytes=100_000)
    assert remaining_files(storage) == []


@pytest.mark.parametrize("password", ["secret", ""], ids=["password-required", "opens-without-password"])
def test_encrypted_pdfs_are_rejected_even_with_an_empty_password(storage, password):
    with pytest.raises(EncryptedPdfNotSupported):
        prepare_pdf(BytesIO(pdf_bytes(password=password)), storage, max_bytes=100_000)
    assert remaining_files(storage) == []


def test_missing_crypto_support_is_a_rejected_encrypted_pdf(storage):
    # Reader initialization can raise this before exposing is_encrypted.
    with (
        patch("voice_archive_api.domain.files.pdfs.PdfReader", side_effect=DependencyError("crypto unavailable")),
        pytest.raises(EncryptedPdfNotSupported),
    ):
        prepare_pdf(BytesIO(pdf_bytes()), storage, max_bytes=100_000)
    assert remaining_files(storage) == []


def test_limit_plus_one_cleans_partial_input(storage):
    payload = pdf_bytes()
    with pytest.raises(FileTooLarge):
        prepare_pdf(BytesIO(payload), storage, max_bytes=len(payload) - 1)
    assert remaining_files(storage) == []


def test_pdf_promotion_failure_removes_staged_input(storage):
    with (
        patch.object(storage, "promote", side_effect=OSError("promotion failed")),
        pytest.raises(OSError, match="promotion failed"),
    ):
        prepare_pdf(BytesIO(pdf_bytes()), storage, max_bytes=100_000)
    assert remaining_files(storage) == []


def test_streaming_input(storage):
    payload = pdf_bytes(3)

    class Source:
        offset = 0

        def read(self, size: int, /) -> bytes:
            assert 0 < size <= 1024 * 1024
            chunk = payload[self.offset : self.offset + min(size, 17)]
            self.offset += len(chunk)
            return chunk

    prepared = prepare_pdf(Source(), storage, max_bytes=len(payload))
    assert prepared.metadata["page_count"] == 3


def test_parser_uses_bounded_reads(storage):
    original_open = storage.open

    class GuardedReader:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            assert size >= 0, "Parser must not request the whole file with read()"
            return self.stream.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

    with patch.object(storage, "open", side_effect=lambda key: GuardedReader(original_open(key))):
        prepared = prepare_pdf(BytesIO(pdf_bytes(3)), storage, max_bytes=100_000)
    assert prepared.metadata["page_count"] == 3


def test_storage_read_error_is_not_intercepted(storage):
    with (
        patch.object(storage, "open", side_effect=OSError("read failed")),
        pytest.raises(OSError, match="read failed"),
    ):
        # This raises an OS error, not an invalid PDF error
        prepare_pdf(BytesIO(pdf_bytes()), storage, max_bytes=100_000)

    assert remaining_files(storage) == []
