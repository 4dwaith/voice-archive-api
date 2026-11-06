from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, String
from sqlalchemy.orm import Mapped, mapped_column

from voice_archive_api.db.base import Base
from voice_archive_api.logic.books.constants import TITLE_LIMIT


class Series(Base):
    __tablename__ = "series"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(), CheckConstraint("id > 0", name="positive_id"), primary_key=True
    )
    title: Mapped[str] = mapped_column(String(TITLE_LIMIT), CheckConstraint("length(title) > 0", name="title_nonempty"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
