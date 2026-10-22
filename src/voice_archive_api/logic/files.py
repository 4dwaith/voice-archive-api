"""File kinds and immutable prepared-asset snapshots for pure validation."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class FileKind(StrEnum):
    COVER = "cover"
    SOURCE_PDF = "source_pdf"
    AUDIO_CHAPTER = "audio_chapter"


@dataclass(frozen=True)
class PreparedAsset:
    """Prepared-file information for callers that do not need an ORM record."""

    id: int
    kind: FileKind
    book_id: int | None
    section_id: int | None
    metadata: Mapping[str, int | str]
