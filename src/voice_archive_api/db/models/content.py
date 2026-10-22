from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from voice_archive_api.db.base import Base
from voice_archive_api.logic.content_validation.kinds import SectionKind, SegmentKind


class BookSection(Base):
    __tablename__ = "sections"
    __table_args__ = (
        # PostgreSQL needs this exact unique pair for composite FKs that enforce
        # same-book parents and segment ownership, even though id alone is unique.
        UniqueConstraint("book_id", "id", name="uq_sections_book_id_id"),
        UniqueConstraint("book_id", "source_key", name="uq_sections_book_source_key"),
        UniqueConstraint("book_id", "sequence", name="uq_sections_book_sequence"),
        UniqueConstraint(
            "book_id",
            "parent_id",
            "sibling_sequence",
            name="uq_sections_sibling_sequence",
            postgresql_nulls_not_distinct=True,
        ),
        ForeignKeyConstraint(["book_id", "parent_id"], ["sections.book_id", "sections.id"]),
        CheckConstraint("id > 0", name="positive_id"),
        CheckConstraint("length(source_key) BETWEEN 1 AND 256", name="source_key_length"),
        CheckConstraint("length(title) BETWEEN 1 AND 2000", name="title_length"),
        CheckConstraint("sibling_sequence >= 0", name="sibling_sequence"),
        CheckConstraint("sequence >= 0", name="sequence"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_self_parent"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    book_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("books.id", ondelete="CASCADE"))
    source_key: Mapped[str] = mapped_column(Text)
    # References another section in the same book, enforced by the composite FK above.
    parent_id: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[SectionKind] = mapped_column(
        Enum(
            SectionKind,
            native_enum=False,
            create_constraint=True,
            values_callable=lambda members: [member.value for member in members],
            name="kind",
        )
    )
    title: Mapped[str] = mapped_column(Text)
    sibling_sequence: Mapped[int] = mapped_column(Integer)
    sequence: Mapped[int] = mapped_column(Integer)


class BookSegment(Base):
    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("book_id", "source_key", name="uq_segments_book_source_key"),
        UniqueConstraint("book_id", "sequence", name="uq_segments_book_sequence"),
        # Checks the pair against one section row so the segment and section share a book.
        ForeignKeyConstraint(["book_id", "section_id"], ["sections.book_id", "sections.id"]),
        Index("ix_segments_page_indexes", "page_indexes", postgresql_using="gin"),
        Index("ix_segments_book_id_section_id", "book_id", "section_id"),
        CheckConstraint("id > 0", name="positive_id"),
        CheckConstraint("length(source_key) BETWEEN 1 AND 256", name="source_key_length"),
        CheckConstraint("sequence >= 0", name="sequence"),
        CheckConstraint("length(html_content) > 0 AND length(text_content) > 0", name="nonempty_text"),
        CheckConstraint("jsonb_typeof(regions) = 'array'", name="regions_array"),
        CheckConstraint(
            """(narration_start_ms IS NULL AND narration_end_ms IS NULL AND narration_seek_ms IS NULL) OR
            (narration_start_ms IS NOT NULL AND narration_end_ms IS NOT NULL AND narration_start_ms >= 0 AND narration_end_ms > narration_start_ms
             AND (narration_seek_ms IS NULL OR (narration_seek_ms >= 0 AND narration_seek_ms <= narration_start_ms)))""",
            name="timing",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    book_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("books.id", ondelete="CASCADE"))
    source_key: Mapped[str] = mapped_column(Text)
    # References a section in the same book, enforced by the composite FK above.
    section_id: Mapped[int | None] = mapped_column(BigInteger)
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[SegmentKind] = mapped_column(
        Enum(
            SegmentKind,
            native_enum=False,
            create_constraint=True,
            values_callable=lambda members: [member.value for member in members],
            name="kind",
        )
    )
    html_content: Mapped[str] = mapped_column(Text)
    text_content: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    regions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"))
    page_indexes: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), Computed("public.region_pages(regions)", persisted=True)
    )
    narration_start_ms: Mapped[int | None] = mapped_column(BigInteger)
    narration_end_ms: Mapped[int | None] = mapped_column(BigInteger)
    narration_seek_ms: Mapped[int | None] = mapped_column(BigInteger)
    # Optional producer vocabulary; no closed alignment-status set is defined yet.
    alignment_status: Mapped[str | None] = mapped_column(Text)
    alignment_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
