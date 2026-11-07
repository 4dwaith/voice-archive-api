from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    select,
    text,
)
from sqlalchemy.orm import Mapped, column_property, mapped_column

from voice_archive_api.db.base import Base
from voice_archive_api.db.constants import UNIQUE_BOOK_IDENTITY, UNIQUE_BOOK_SERIES_INDEX
from voice_archive_api.db.models.file import StoredFile
from voice_archive_api.db.models.series import Series
from voice_archive_api.logic.books.constants import AUTHOR_LIMIT, DESCRIPTION_LIMIT, TITLE_LIMIT
from voice_archive_api.logic.files import FileKind


class Book(Base):
    __tablename__ = "books"
    __table_args__ = (
        CheckConstraint(
            "(series_id IS NULL AND series_index IS NULL) OR (series_id IS NOT NULL AND series_index > 0 AND series_index IS NOT NULL)",
            name="series_membership",
        ),
        UniqueConstraint(
            "series_id", "series_index", name=UNIQUE_BOOK_SERIES_INDEX, deferrable=True, initially="DEFERRED"
        ),
        CheckConstraint(
            """(content_version IS NULL AND content_schema_version IS NULL AND content_imported_at IS NULL)
            OR (content_version IS NOT NULL AND content_schema_version IS NOT NULL
                AND content_schema_version > 0 AND content_imported_at IS NOT NULL)""",
            name="content_metadata",
        ),
        # Fixed-size index expressions avoid long Unicode text.
        Index(
            UNIQUE_BOOK_IDENTITY,
            text("digest(normalized_title, 'sha256')"),
            text("digest(normalized_author, 'sha256')"),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        CheckConstraint("id > 0", name="positive_id"),
        primary_key=True,
    )
    title: Mapped[str] = mapped_column(String(TITLE_LIMIT), CheckConstraint("length(title) > 0", name="title_nonempty"))
    normalized_title: Mapped[str] = mapped_column(
        Text,
        CheckConstraint("length(normalized_title) > 0", name="normalized_title_nonempty"),
    )
    author: Mapped[str | None] = mapped_column(String(AUTHOR_LIMIT))
    normalized_author: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(String(DESCRIPTION_LIMIT))
    summary: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text, CheckConstraint("length(language) > 0", name="language_nonempty"))
    cover_file_id: Mapped[int | None] = column_property(
        select(StoredFile.id)
        .where(StoredFile.book_id == id, StoredFile.kind == FileKind.COVER)
        .correlate_except(StoredFile)
        .scalar_subquery()
    )
    source_pdf_file_id: Mapped[int | None] = column_property(
        select(StoredFile.id)
        .where(StoredFile.book_id == id, StoredFile.kind == FileKind.SOURCE_PDF)
        .correlate_except(StoredFile)
        .scalar_subquery()
    )
    series_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("series.id", ondelete="RESTRICT"), index=True)
    series_index: Mapped[int | None] = mapped_column(Integer)
    series_title: Mapped[str | None] = column_property(
        select(Series.title).where(Series.id == series_id).correlate_except(Series).scalar_subquery()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    content_version: Mapped[UUID | None] = mapped_column(Uuid)
    content_schema_version: Mapped[int | None] = mapped_column(Integer)
    content_imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
