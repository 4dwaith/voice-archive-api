from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from voice_archive_api.books.constants import (
    AUTHOR_LIMIT,
    DESCRIPTION_LIMIT,
    TITLE_LIMIT,
    UNIQUE_BOOK_IDENTITY,
)
from voice_archive_api.db.base import Base


class Book(Base):
    __tablename__ = "books"
    __table_args__ = (
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
    language: Mapped[str] = mapped_column(Text, CheckConstraint("length(language) > 0", name="language_nonempty"))
    cover_file_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("cover_files.id"),
        unique=True,
    )
    source_pdf_file_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("source_pdf_files.id"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
