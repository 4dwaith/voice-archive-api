"""Materialized window text and multiple planned embeddings per window."""

from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from voice_archive_api.db.base import Base


class RetrievalWindow(Base):
    __tablename__ = "retrieval_windows"
    __table_args__ = (
        UniqueConstraint("book_id", "sequence", name="uq_retrieval_windows_book_sequence"),
        Index(
            "ix_retrieval_windows_english",
            "english_vector",
            postgresql_using="gin",
            postgresql_where=text("translated_text IS NULL"),
        ),
        Index(
            "ix_retrieval_windows_trigrams",
            "trigram_text",
            postgresql_using="gin",
            postgresql_ops={"trigram_text": "gin_trgm_ops"},
            postgresql_where=text("translated_text IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    book_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("books.id", ondelete="CASCADE"))
    content_version: Mapped[UUID] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(Integer)
    section_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sections.id", ondelete="CASCADE"))
    segment_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger))
    path: Mapped[str] = mapped_column(Text)
    primary_text: Mapped[str] = mapped_column(Text)
    translated_text: Mapped[str | None] = mapped_column(Text)

    english_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "CASE WHEN translated_text IS NULL THEN to_tsvector('english'::regconfig, primary_text) END", persisted=True
        ),
    )
    trigram_text: Mapped[str | None] = mapped_column(
        Text, Computed("CASE WHEN translated_text IS NOT NULL THEN normalize(primary_text, NFC) END", persisted=True)
    )


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
