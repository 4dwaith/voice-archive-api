from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from voice_archive_api.logic.library import EvidenceSegment


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1, max_length=8000, pattern=r"\S")
    query_en: str | None = Field(default=None, min_length=1, max_length=8000, pattern=r"\S")
    lexical_terms: list[Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]] | None = Field(
        default=None, min_length=1, max_length=8
    )
    book_id: int | None = Field(default=None, gt=0, le=9_223_372_036_854_775_807)
    series_id: int | None = Field(default=None, gt=0, le=9_223_372_036_854_775_807)
    use_whole_book: bool = False
    effort: Literal["low", "medium", "high"] = "low"

    @model_validator(mode="after")
    def validate_scope(self) -> AskRequest:
        if self.book_id is not None and self.series_id is not None:
            raise ValueError("Choose at most one book or series")
        if self.use_whole_book and self.book_id is None:
            raise ValueError("Whole-book answering requires a book")
        return self


class AnswerReference(EvidenceSegment):
    relevance: str


class AskResult(BaseModel):
    status: Literal["answered", "insufficient_evidence", "unavailable", "oversize", "stale"]
    answer: str
    limitations: list[str]
    references: list[AnswerReference] = Field(default_factory=list)
