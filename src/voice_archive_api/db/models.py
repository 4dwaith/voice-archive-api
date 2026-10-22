"""Register complete model metadata for migrations and application sessions."""

from voice_archive_api.domain.books.model import Book
from voice_archive_api.domain.content.model import BookSection, BookSegment
from voice_archive_api.domain.files.model import FileCleanupProgress, PendingFileDeletion, StoredFile

__all__ = ["Book", "StoredFile", "PendingFileDeletion", "FileCleanupProgress", "BookSection", "BookSegment"]
