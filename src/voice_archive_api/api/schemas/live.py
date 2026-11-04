from typing import Literal

from pydantic import BaseModel, Field

from voice_archive_api.api.schemas.errors import ErrorResponse


class LiveTokenResponse(BaseModel):
    token: str = Field(pattern=r"\S")
    model: str = Field(pattern=r"\S")


class LiveErrorResponse(ErrorResponse[Literal["live_unavailable"]]):
    pass
