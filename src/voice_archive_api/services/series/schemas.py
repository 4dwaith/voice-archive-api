from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.experimental.missing_sentinel import MISSING

from voice_archive_api.services.books.schemas import Title

SeriesTitle = Annotated[Title, Field(pattern=r"^[^\x00]*$")]


class SeriesCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: SeriesTitle


class SeriesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: SeriesTitle | MISSING = MISSING


class SeriesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Annotated[int, Field(gt=0)]
    title: SeriesTitle
    created_at: datetime
    updated_at: datetime


class SeriesDetail(SeriesResponse):
    book_ids: list[int]


class SeriesList(BaseModel):
    items: list[SeriesResponse]
    limit: int
    offset: int
    total: int
