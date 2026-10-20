from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class CoverFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Annotated[int, Field(gt=0)]
    byte_size: Annotated[int, Field(gt=0)]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    created_at: datetime


class SourcePdfFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Annotated[int, Field(gt=0)]
    byte_size: Annotated[int, Field(gt=0)]
    page_count: Annotated[int, Field(gt=0)]
    created_at: datetime
