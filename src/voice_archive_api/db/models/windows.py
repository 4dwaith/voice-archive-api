"""Materialized window text and multiple planned embeddings per window."""

from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from voice_archive_api.db.base import Base


class RetrievalWindow(Base):
    __tablename__ = "retrieval_windows"
    __table_args__ = (UniqueConstraint("book_id", "sequence", name="uq_retrieval_windows_book_sequence"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    book_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("books.id", ondelete="CASCADE"))
    content_version: Mapped[UUID] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(Integer)
    section_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sections.id", ondelete="CASCADE"))
    segment_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger))
    path: Mapped[str] = mapped_column(Text)
    primary_text: Mapped[str] = mapped_column(Text)
    translated_text: Mapped[str | None] = mapped_column(Text)


class WindowEmbedding(Base):
    __tablename__ = "window_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "window_id", "kind", "model", "dimensions", "input_format", name="uq_window_embedding_profile"
        ),
        CheckConstraint(
            "vector IS NULL OR vector_dims(vector) = dimensions", name="window_embedding_vector_dimensions"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    window_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("retrieval_windows.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(
        Text, CheckConstraint("kind IN ('primary', 'translated')", name="window_embedding_kind")
    )
    model: Mapped[str] = mapped_column(Text)
    dimensions: Mapped[int] = mapped_column(
        Integer, CheckConstraint("dimensions > 0", name="window_embedding_dimensions")
    )
    input_format: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(String(64))
    vector: Mapped[list[float] | None] = mapped_column(VECTOR())
