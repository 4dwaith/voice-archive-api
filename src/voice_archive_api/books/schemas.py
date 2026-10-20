from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic.experimental.missing_sentinel import MISSING

from voice_archive_api.books.constants import (
    AUTHOR_LIMIT,
    DESCRIPTION_LIMIT,
    TITLE_LIMIT,
)

from voice_archive_api.books.validation import (
    validate_author,
    validate_language,
    validate_text,
    validate_title,
)

Title = Annotated[
    str,
    Field(min_length=1, max_length=TITLE_LIMIT),
    AfterValidator(validate_text),
    AfterValidator(validate_title),
]
Author = Annotated[
    str,
    Field(max_length=AUTHOR_LIMIT),
    AfterValidator(validate_text),
    AfterValidator(validate_author),
]
Description = Annotated[
    str,
    Field(max_length=DESCRIPTION_LIMIT),
    AfterValidator(validate_text),
]
Language = Annotated[
    str,
    Field(
        min_length=1,
        description="Required language code validated by langcodes (for example en or kn)",
        examples=["en", "kn-IN"],
    ),
    AfterValidator(validate_language),
]
FileId = Annotated[int, Field(strict=True, gt=0, le=9_223_372_036_854_775_807)]


class BookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    language: Language
    author: Author | None = None
    description: Description | None = None
    cover_file_id: FileId | None = None
    source_pdf_file_id: FileId | None = None


class BookPatch(BaseModel):
    """Omitted fields keep their stored value; null clears optional fields."""

    model_config = ConfigDict(extra="forbid")

    title: Title | MISSING = MISSING
    language: Language | MISSING = MISSING
    author: Author | None | MISSING = MISSING
    description: Description | None | MISSING = MISSING
    cover_file_id: FileId | None | MISSING = MISSING
    source_pdf_file_id: FileId | None | MISSING = MISSING


class BookResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Annotated[int, Field(gt=0)]
    title: Title
    language: Language
    author: Author | None
    description: Description | None
    cover_file_id: FileId | None
    source_pdf_file_id: FileId | None
    created_at: datetime
    updated_at: datetime


class BookList(BaseModel):
    items: list[BookResponse]
    limit: int
    offset: int
    total: int
