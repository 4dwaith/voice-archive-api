"""Durable book requests and the current globally exclusive provider batch."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Enum, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from voice_archive_api.db.base import Base
from voice_archive_api.logic.batches import BookBatchJobState


class BookBatchJob(Base):
    __tablename__ = "book_batch_jobs"

    # Operational records survive source deletion until accepted work is collected.
    book_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    state: Mapped[BookBatchJobState] = mapped_column(
        Enum(
            BookBatchJobState,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
            name="book_batch_job_state",
        )
    )
    active_batch_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp())
