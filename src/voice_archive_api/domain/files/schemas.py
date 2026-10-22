from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from voice_archive_api.domain.files.model import FileKind

PositiveMetadataInteger = Annotated[int, Field(strict=True, gt=0, le=2_147_483_647)]


class CoverMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    width: PositiveMetadataInteger
    height: PositiveMetadataInteger


class SourcePdfMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    page_count: PositiveMetadataInteger


class FileResponseBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Annotated[int, Field(gt=0)]
    byte_size: Annotated[int, Field(gt=0)]
    created_at: datetime


class CoverFileResponse(FileResponseBase):
    kind: Literal[FileKind.COVER]
    metadata: CoverMetadata = Field(validation_alias="file_metadata")


class SourcePdfFileResponse(FileResponseBase):
    kind: Literal[FileKind.SOURCE_PDF]
    metadata: SourcePdfMetadata = Field(validation_alias="file_metadata")


type FileResponse = Annotated[CoverFileResponse | SourcePdfFileResponse, Field(discriminator="kind")]
