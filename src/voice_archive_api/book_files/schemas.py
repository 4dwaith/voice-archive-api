from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from voice_archive_api.book_files.model import FileKind


class FileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Annotated[int, Field(gt=0)]
    kind: FileKind
    byte_size: Annotated[int, Field(gt=0)]
    metadata: dict[str, int | str] = Field(validation_alias="file_metadata")
    created_at: datetime
