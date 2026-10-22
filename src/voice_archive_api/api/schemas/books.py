from typing import Literal

from voice_archive_api.api.schemas.errors import ErrorResponse

type BookErrorCode = Literal[
    "book_not_found",
    "duplicate_book",
    "file_already_linked",
    "content_purge_required",
]


class BookErrorResponse(ErrorResponse[BookErrorCode]):
    """HTTP error envelope for book operations."""
