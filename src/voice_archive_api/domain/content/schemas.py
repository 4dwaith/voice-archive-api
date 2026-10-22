from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class ContentManifest(BaseModel):
    state: Literal["empty", "pdf_only", "published"]
    content_version: UUID | None
    schema_version: int | None
    imported_at: datetime | None
    source_pdf_file_id: int | None


class ImportResult(BaseModel):
    dry_run: bool
    section_count: int
    segment_count: int
    content_version: UUID | None
