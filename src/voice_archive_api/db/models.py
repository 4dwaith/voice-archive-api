"""Import every domain model here so Alembic sees the complete metadata."""

from voice_archive_api.books.model import Book
from voice_archive_api.book_files.model import CoverFile, SourcePdfFile

__all__ = ["Book", "CoverFile", "SourcePdfFile"]
