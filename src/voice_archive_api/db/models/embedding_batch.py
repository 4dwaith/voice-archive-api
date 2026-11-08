"""Durable submissions and immutable request identities for embedding batches."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from voice_archive_api.db.base import Base


class EmbeddingBatchJob(Base):
    __tablename__ = "embedding_batch_jobs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    book_id: Mapped[int] = mapped_column(BigInteger)
    model: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    provider_name: Mapped[str | None] = mapped_column(Text, unique=True)
    input_file_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmbeddingBatchRequest(Base):
    __tablename__ = "embedding_batch_requests"
    __table_args__ = (
        Index(
            "uq_embedding_request_active_input",
            "embedding_id",
            "input_hash",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("embedding_batch_jobs.id", ondelete="CASCADE"), index=True)
    # Keep submitted identities even when rebuilding/deleting their source windows.
    embedding_id: Mapped[UUID] = mapped_column(Uuid)
    input_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean)
