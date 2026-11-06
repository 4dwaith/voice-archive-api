"""Register complete model metadata for migrations and application sessions."""

from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.file import FileCleanupProgress, PendingFileDeletion, StoredFile
from voice_archive_api.db.models.series import Series

__all__ = ["Series", "Book", "StoredFile", "PendingFileDeletion", "FileCleanupProgress", "BookSection", "BookSegment"]
