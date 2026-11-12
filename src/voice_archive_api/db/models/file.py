from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    FetchedValue,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from voice_archive_api.db.base import Base
from voice_archive_api.db.constants import UNIQUE_FILE_BOOK_KIND
from voice_archive_api.logic.files import FileKind


def positive_metadata_integer(field: str) -> str:
    """Require a present JSON integer within the legacy PostgreSQL INTEGER range."""
    return f"""CASE
        WHEN jsonb_typeof(metadata->'{field}') = 'number'
             AND (metadata->>'{field}') ~ '^[1-9][0-9]*$'
        THEN (metadata->>'{field}')::numeric <= 2147483647
        ELSE false END"""


class StoredFile(Base):
    """Prepared immutable bytes; kind-specific metadata is extracted on upload."""

    __tablename__ = "files"
    __table_args__ = (
        Index("ix_files_unlinked_at_id", "unlinked_at", "id"),
        UniqueConstraint("book_id", "kind", name=UNIQUE_FILE_BOOK_KIND),
        UniqueConstraint("section_id", "kind", name="uq_files_section_kind"),
        CheckConstraint("book_id IS NULL OR section_id IS NULL", name="exclusive_owner"),
        CheckConstraint(
            "(book_id IS NULL AND section_id IS NULL) = (unlinked_at IS NOT NULL)", name="ownership_timestamp"
        ),
        CheckConstraint("section_id IS NULL OR kind = 'audio_chapter'", name="section_kind"),
        CheckConstraint("book_id IS NULL OR kind IN ('cover', 'source_pdf')", name="book_kind"),
        CheckConstraint(
            f"kind <> 'cover' OR ({positive_metadata_integer('width')} AND {positive_metadata_integer('height')})",
            name="cover_metadata",
        ),
        CheckConstraint(
            f"kind <> 'source_pdf' OR ({positive_metadata_integer('page_count')})", name="source_pdf_metadata"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        CheckConstraint("id > 0", name="positive_id"),
        primary_key=True,
    )
    kind: Mapped[FileKind] = mapped_column(
        Enum(
            FileKind,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
            name="file_kind",
        ),
    )
    storage_key: Mapped[str] = mapped_column(
        Text,
        CheckConstraint("length(storage_key) > 0", name="storage_key_nonempty"),
        unique=True,
    )
    byte_size: Mapped[int] = mapped_column(
        BigInteger,
        CheckConstraint("byte_size > 0", name="positive_byte_size"),
    )
    file_metadata: Mapped[dict[str, int | str]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    uploaded_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), server_default=text("1"), index=True
    )
    book_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("books.id", ondelete="SET NULL"))
    section_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sections.id", ondelete="SET NULL"))
    unlinked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=FetchedValue(), server_onupdate=FetchedValue()
    )


class PendingFileDeletion(Base):
    """A pending deletion, committed before removing stored bytes."""

    __tablename__ = "pending_file_deletions"

    storage_key: Mapped[str] = mapped_column(
        Text,
        CheckConstraint("length(storage_key) > 0", name="storage_key_nonempty"),
        primary_key=True,
    )
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FileCleanupProgress(Base):
    """One row holding the completed scan boundary for prepared files."""

    __tablename__ = "file_cleanup_progress"

    id: Mapped[int] = mapped_column(
        Integer,
        CheckConstraint("id = 1", name="singleton"),
        primary_key=True,
        autoincrement=False,
    )
    cleanup_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
