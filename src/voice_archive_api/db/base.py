from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Single SQLAlchemy model registry used by the app and Alembic."""

