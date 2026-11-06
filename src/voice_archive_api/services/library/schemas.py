from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from voice_archive_api.logic.library import SourceSegment


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1, max_length=8000, pattern=r"\S")
    book_id: int = Field(gt=0, le=9_223_372_036_854_775_807)
    effort: Literal["low", "medium", "high"] = "low"


class AnswerReference(SourceSegment):
    book_id: int
    content_version: UUID
    relevance: str


class AskResult(BaseModel):
    status: Literal["answered", "insufficient_evidence", "unavailable", "oversize", "stale"]
    answer: str
    limitations: list[str]
    references: list[AnswerReference] = Field(default_factory=list)
