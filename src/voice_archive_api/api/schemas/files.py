from typing import Literal

from voice_archive_api.api.schemas.errors import ErrorResponse

type FileErrorCode = Literal[
    "file_not_found",
    "file_already_linked",
    "file_kind_mismatch",
    "invalid_image",
    "unsupported_media_type",
    "invalid_pdf",
    "encrypted_pdf_not_supported",
    "invalid_audio",
]


class FileErrorResponse(ErrorResponse[FileErrorCode]):
    """File operation and preparation errors."""


class FileUploadErrorResponse(ErrorResponse[Literal["file_too_large", "invalid_multipart"]]):
    """Errors shared by cover, PDF, and MP3 upload requests."""
