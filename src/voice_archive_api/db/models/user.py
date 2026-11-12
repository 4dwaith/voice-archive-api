from sqlalchemy import BigInteger, Boolean, CheckConstraint, Identity, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from voice_archive_api.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_email", text("lower(email)"), unique=True),
        CheckConstraint("email IS NOT NULL OR password_hash IS NULL", name="email_less_password"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(), CheckConstraint("id > 0", name="positive_id"), primary_key=True
    )
    email: Mapped[str | None] = mapped_column(
        String(320), CheckConstraint("email IS NULL OR length(email) > 0", name="email_nonempty")
    )
    display_name: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        CheckConstraint(
            "NOT is_enabled OR (email IS NOT NULL AND password_hash IS NOT NULL)", name="enabled_credentials"
        ),
        default=False,
    )
