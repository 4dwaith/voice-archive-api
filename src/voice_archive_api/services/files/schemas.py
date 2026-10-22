from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from voice_archive_api.logic.files import FileKind

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


class AudioMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    duration_ms: Annotated[int, Field(strict=True, gt=0, le=9_223_372_036_854_775_807)]
    sample_rate: PositiveMetadataInteger
    channels: PositiveMetadataInteger


class AudioFileResponse(FileResponseBase):
    kind: Literal[FileKind.AUDIO_CHAPTER]
    metadata: AudioMetadata = Field(validation_alias="file_metadata")


type FileResponse = Annotated[
    CoverFileResponse | SourcePdfFileResponse | AudioFileResponse, Field(discriminator="kind")
]
