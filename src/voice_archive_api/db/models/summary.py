from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from voice_archive_api.db.base import Base


class SummaryGenerationRun(Base):
    __tablename__ = "book_summary_runs"
    __table_args__ = (CheckConstraint("(state = 'running') = (finished_at IS NULL)", name="summary_run_finished"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    book_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("books.id", ondelete="CASCADE"), index=True)
    root_section_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sections.id", name="fk_summary_run_root", ondelete="CASCADE")
    )
    allow_stale_downstream: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    input_fingerprint: Mapped[str] = mapped_column("source_fingerprint", String(64))
    state: Mapped[str] = mapped_column(
        Text, CheckConstraint("state IN ('running', 'completed', 'failed')", name="summary_run_state")
    )
    model: Mapped[str] = mapped_column(Text)
    policy: Mapped[str] = mapped_column(Text)
    source_language: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(Text)

    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    thinking_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    generation_response_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    missing_usage_response_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class SummaryBatchCheckpoint(Base):
    __tablename__ = "book_summary_batch_checkpoints"

    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("book_summary_runs.id", ondelete="CASCADE"), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_kind: Mapped[str] = mapped_column(
        Text, CheckConstraint("target_kind IN ('book', 'section')", name="summary_checkpoint_target_kind")
    )
    target_id: Mapped[int] = mapped_column(BigInteger)
    batch_index: Mapped[int] = mapped_column(
        Integer, CheckConstraint("batch_index > 0", name="summary_checkpoint_batch_index")
    )
    summaries: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
