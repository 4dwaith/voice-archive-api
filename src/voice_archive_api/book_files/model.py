from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from voice_archive_api.db.base import Base


class StoredFileBase(Base):
    """Abstract ORM base whose storage columns are copied into each file table."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(), CheckConstraint("id > 0", name="positive_id"),
        primary_key=True,
    )
    storage_key: Mapped[str] = mapped_column(
        Text, CheckConstraint("length(storage_key) > 0", name="storage_key_nonempty"),
        unique=True,
    )
    byte_size: Mapped[int] = mapped_column(
        BigInteger, CheckConstraint("byte_size > 0", name="positive_byte_size"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CoverFile(StoredFileBase):
    """A prepared WebP cover, ready to be linked to a book."""

    __tablename__ = "cover_files"

    width: Mapped[int] = mapped_column(
        Integer, CheckConstraint("width > 0", name="positive_width"),
    )
    height: Mapped[int] = mapped_column(
        Integer, CheckConstraint("height > 0", name="positive_height"),
    )


class SourcePdfFile(StoredFileBase):
    """A prepared original PDF, ready to be linked to a book."""

    __tablename__ = "source_pdf_files"

    page_count: Mapped[int] = mapped_column(
        Integer, CheckConstraint("page_count > 0", name="positive_page_count"),
    )
