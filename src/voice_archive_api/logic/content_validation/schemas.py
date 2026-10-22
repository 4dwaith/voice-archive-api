"""Canonical v1 input only. No independent text, page projection, or audio FK."""

import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, PrivateAttr, field_validator, model_validator

from voice_archive_api.logic.books.validation import validate_language, validate_text, validate_title
from voice_archive_api.logic.content_validation.html import MAX_HTML_BYTES, parse_fragment
from voice_archive_api.logic.content_validation.kinds import SectionKind, SegmentKind

MAX_SECTIONS = 50_000
MAX_SEGMENTS = 100_000
MAX_REGIONS = 256
MAX_PAYLOAD_BYTES = 32_000_000
PositiveInteger = Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)]
FileId = Annotated[int, Field(strict=True, gt=0, le=9_223_372_036_854_775_807)]
Millis = Annotated[int, Field(strict=True, ge=0, le=9_223_372_036_854_775_807)]
SourceKey = Annotated[str, Field(min_length=1, max_length=256)]
Coordinate = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


# Rules to apply to every content model
class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, strict=True)

    @field_validator("*", mode="before")
    @classmethod
    def unicode_strings(cls, value: object) -> object:
        if isinstance(value, str):
            validate_text(value)
            if "\x00" in value:
                raise ValueError("NUL cannot be stored in PostgreSQL text/JSONB")
        return value


class Region(InputModel):
    page_index: PositiveInteger
    x0: Coordinate
    y0: Coordinate
    x1: Coordinate
    y1: Coordinate

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.x0 >= self.x1 or self.y0 >= self.y1:
            raise ValueError("Region requires x0 < x1 and y0 < y1")
        return self


class SectionInput(InputModel):
    source_key: SourceKey
    parent_source_key: SourceKey | None = None
    kind: Annotated[SectionKind, Field(strict=False)]
    title: Annotated[str, Field(min_length=1, max_length=2000)]
    sibling_sequence: PositiveInteger
    audio_file_id: FileId | None = None

    _title = field_validator("title")(validate_title)


class SegmentInput(InputModel):
    source_key: SourceKey
    section_source_key: SourceKey | None = None
    sequence: PositiveInteger
    kind: Annotated[SegmentKind, Field(strict=False)]
    html_content: Annotated[str, Field(min_length=1, max_length=MAX_HTML_BYTES)]
    language: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    regions: Annotated[list[Region], Field(max_length=MAX_REGIONS)] = Field(default_factory=list)

    narration_start_ms: Millis | None = None
    narration_end_ms: Millis | None = None
    narration_seek_ms: Millis | None = None

    # Passive producer metadata; deliberately not a closed enum.
    alignment_status: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    alignment_metadata: dict[str, JsonValue] | None = None

    # Ensures that this content can't be written to.
    _text_content: str = PrivateAttr(default="")

    @field_validator("language")
    @classmethod
    def language_code(cls, value: str | None) -> str | None:
        return validate_language(value) if value is not None else None

    @field_validator("alignment_metadata")
    @classmethod
    def metadata_jsonb_compatible(cls, value: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
        serialized.encode("utf-8")  # Reject invalid Unicode, including in nested values and keys.
        # Ignore escaped literal backslashes: the text "\\u0000" is valid, an actual NUL is not.
        if r"\u0000" in serialized.replace(r"\\", ""):
            raise ValueError("NUL cannot be stored in PostgreSQL JSONB")
        return value

    @model_validator(mode="after")
    def timing(self) -> Self:
        if self.narration_start_ms is None and self.narration_end_ms is None and self.narration_seek_ms is None:
            return self
        if (
            self.narration_start_ms is None
            or self.narration_end_ms is None
            or self.narration_start_ms >= self.narration_end_ms
            or (self.narration_seek_ms is not None and self.narration_seek_ms > self.narration_start_ms)
        ):
            raise ValueError(
                "Require narration_start_ms/narration_end_ms together, narration_start_ms < narration_end_ms, and narration_seek_ms <= narration_start_ms"
            )
        return self

    @property
    def text_content(self) -> str:
        """Available after canonical validation; never an independently editable input."""
        return self._text_content

    def derive_html(self) -> None:
        self.html_content, self._text_content = parse_fragment(self.html_content, self.kind)


class ContentInput(InputModel):
    @field_validator("schema_version", mode="before")
    @classmethod
    def version_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be integer 1")
        return value

    schema_version: Literal[1]
    source_pdf_file_id: FileId | None = None
    sections: Annotated[list[SectionInput], Field(max_length=MAX_SECTIONS)]
    segments: Annotated[list[SegmentInput], Field(min_length=1, max_length=MAX_SEGMENTS)]
