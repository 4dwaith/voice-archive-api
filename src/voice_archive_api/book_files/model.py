from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Enum, Identity, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from voice_archive_api.db.base import Base
from voice_archive_api.file_handling import BinarySource, Storage


class FileKind(StrEnum):
    COVER = "cover"
    SOURCE_PDF = "source_pdf"


@dataclass(frozen=True)
class PreparedFile:
    storage_key: str
    byte_size: int
    metadata: dict[str, int | str]


class Preparer(Protocol):
    def __call__(self, source: BinarySource, storage: Storage, *, max_bytes: int) -> PreparedFile: ...


@dataclass(frozen=True)
class FileStrategy:
    prepare: Preparer
    limit_setting_key: str
    media_type: str
    filename: str


class StoredFile(Base):
    """Prepared immutable bytes; kind-specific metadata is extracted on upload."""

    __tablename__ = "files"
    __table_args__ = (Index("ix_files_created_at_id", "created_at", "id"),)

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
