from typing import BinaryIO

from pypdf import PdfReader
from pypdf.errors import DependencyError, PyPdfError

from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.file_handling import BinarySource, Storage
from voice_archive_api.services.files.types import PreparedFile


class InvalidPdf(ApplicationError):
    """The input is not a structurally readable PDF with at least one page."""

    code = "invalid_pdf"
    message = "Source file must be a readable PDF with at least one page"


class EncryptedPdfNotSupported(ApplicationError):
    """Encrypted PDFs require a password workflow that is not supported yet."""

    code = "encrypted_pdf_not_supported"
    message = "Encrypted PDFs are not supported"


def _inspect_pdf(source: BinaryIO) -> tuple[int, int]:
    source.seek(0, 2)
    byte_size = source.tell()
    source.seek(0)
    if source.read(5) != b"%PDF-":
        raise InvalidPdf
    source.seek(0)
    try:
        # Passing the open stream matters: passing a path makes pypdf copy the
        # whole file into BytesIO. Strict mode rejects damaged structures rather
        # than attempting repair. Page contents/images are not rendered here.
        with PdfReader(source, strict=True) as reader:
            if reader.is_encrypted:
                raise EncryptedPdfNotSupported
            page_count = len(reader.pages)
            if page_count == 0:
                raise InvalidPdf
    except DependencyError as exc:
        # pypdf may try an empty password during reader initialization. Opening
        # AES-encrypted PDFs can therefore require its optional crypto package
        # before is_encrypted can be checked. Reject instead of adding decrypt support.
        raise EncryptedPdfNotSupported from exc
    except (PyPdfError, ValueError, TypeError, KeyError, IndexError, RecursionError, NotImplementedError) as exc:
        raise InvalidPdf from exc
    return byte_size, page_count


def prepare_pdf(source: BinarySource, storage: Storage, *, max_bytes: int) -> PreparedFile:
    """Stage, inspect, then promote a detached source PDF without rewriting it.

    Incoming bytes use bounded storage reads. Parsing seeks through the staged
    file, but parser indexes and page objects still consume document-dependent
    memory. This validates structure/page count, not every page's renderability.
    The caller owns source and the returned promoted file.
    """
    temporary_key = storage.put_temporary(source, max_bytes=max_bytes)
    promoted_key = None
    try:
        with storage.open(temporary_key) as staged:
            byte_size, page_count = _inspect_pdf(staged)

        # Close the validation reader before unlinking the temporary name.
        # Unlinking may not work with file handles open
        promoted_key = storage.promote(temporary_key)
        return PreparedFile(promoted_key, byte_size, {"page_count": page_count})
    except BaseException:
        if promoted_key is not None:
            storage.delete(promoted_key)
        storage.delete(temporary_key)
        raise
