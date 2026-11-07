from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from voice_archive_api.logic.content_validation.kinds import SectionKind, SegmentKind
from voice_archive_api.logic.content_validation.schemas import Region
from voice_archive_api.services.files.schemas import AudioMetadata, SourcePdfMetadata


class ContentFile(BaseModel):
    id: int
    kind: Literal["source_pdf", "audio_chapter"]
    book_id: int | None
    section_id: int | None
    byte_size: int
    metadata: AudioMetadata | SourcePdfMetadata
    url: str


class ContentManifest(BaseModel):
    summary: str | None
    state: Literal["empty", "pdf_only", "published"]
    content_version: UUID | None
    schema_version: int | None
    imported_at: datetime | None
    source_pdf_file_id: int | None
    section_count: int
    segment_count: int
    files: list[ContentFile]


class SectionRead(BaseModel):
    summary: str | None
    id: int
    source_key: str
    parent_id: int | None
    kind: SectionKind
    title: str
    sibling_sequence: int
    sequence: int
    audio_file_id: int | None
    first_segment_source_key: str | None


class SegmentRead(BaseModel):
    summary: str | None
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_key: str
    section_id: int | None
    sequence: int
    kind: SegmentKind
    html_content: str
    text_content: str
    language: str | None
    regions: list[Region]
    page_indexes: list[int]
    narration_start_ms: int | None
    narration_end_ms: int | None
    narration_seek_ms: int | None
    alignment_status: str | None
    alignment_metadata: dict[str, object] | None


class SectionPage(BaseModel):
    content_version: UUID
    items: list[SectionRead]
    next_offset: int | None


class SegmentPage(BaseModel):
    content_version: UUID
    items: list[SegmentRead]
    next_offset: int | None


class ImportResult(BaseModel):
    dry_run: bool
    section_count: int
    segment_count: int
    content_version: UUID | None
