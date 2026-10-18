"""Import every domain model here so Alembic sees the complete metadata."""

from voice_archive_api.books.model import Book

__all__ = ["Book"]
