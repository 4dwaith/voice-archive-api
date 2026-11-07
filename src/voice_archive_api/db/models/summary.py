from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from voice_archive_api.db.base import Base


class SummaryGenerationRun(Base):
    __tablename__ = "book_summary_runs"
    __table_args__ = (
        CheckConstraint("state IN ('running', 'completed', 'failed')", name="summary_run_state"),
        CheckConstraint("(state = 'running') = (finished_at IS NULL)", name="summary_run_finished"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    book_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("books.id", ondelete="CASCADE"), index=True)
    root_section_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sections.id", name="fk_summary_run_root", ondelete="CASCADE")
    )
    allow_stale_downstream: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    input_fingerprint: Mapped[str] = mapped_column("source_fingerprint", String(64))
    state: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    policy: Mapped[str] = mapped_column(Text)
    source_language: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(Text)
