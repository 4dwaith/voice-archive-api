from typing import Literal

from voice_archive_api.api.schemas.errors import ErrorResponse


class SeriesErrorResponse(ErrorResponse[Literal["series_not_found", "series_order_conflict"]]):
    """HTTP error envelope for series operations."""
